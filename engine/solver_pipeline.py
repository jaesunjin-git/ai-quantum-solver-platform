from __future__ import annotations
import os
# engine/solver_pipeline.py
# ============================================================
# Solver Pipeline: 수학 모델 IR -> 컴파일 -> 실행 -> 결과 통합
#
# 구조:
#   BaseSolverPipeline (ABC) — problem type 무관 공통 흐름
#     └── SolverPipeline    — crew scheduling (SP/IR + Hybrid)
#
# Material Science 등 새 problem type은 BaseSolverPipeline을 상속하여
# _compile()과 _convert_result()만 구현하면 됨.
# ============================================================


import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.compiler import get_compiler, get_compiler_unified
from engine.compiler.base import DataBinder, CompileResult
from engine.result_interpreter import interpret_result, save_artifacts
from engine.executor import get_executor
from engine.gates.gate3_compile_check import run as run_gate3
from engine.executor.base import ExecuteResult

logger = logging.getLogger(__name__)


# ============================================================
# Pipeline Result
# ============================================================
@dataclass
class PipelineResult:
    """전체 파이프라인 결과"""
    success: bool
    phase: str = ""                     # "compile", "execute", "complete"
    solver_id: str = ""
    solver_name: str = ""

    # Compile info
    compile_result: Optional[CompileResult] = None
    compile_time_sec: float = 0.0

    # Execute info
    execute_result: Optional[ExecuteResult] = None

    # Summary (프론트엔드용)
    summary: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class PipelineContext:
    """한 번의 파이프라인 실행에 필요한 모든 mutable 상태.
    run() 시작 시 생성, 각 phase에 전달, 실행 종료 후 폐기.
    재사용 시 이전 실행 상태 오염 방지."""
    project_id: str = ""
    gate3_result: Optional[Dict[str, Any]] = None
    stage5_validation: Optional[Dict[str, Any]] = None
    sp_duty_map: Optional[Dict[int, Any]] = None
    sp_duties: Optional[list] = None
    sp_problem: Optional[Any] = None  # SetPartitioningProblem (side constraint 결과 표시용)


# ============================================================
# Pipeline Runner
# ============================================================
# Base Pipeline (problem type 무관)
# ============================================================
class BaseSolverPipeline(ABC):
    """problem type에 무관한 solver 파이프라인 기반 클래스.

    공통 흐름:
      1. _bind_data()      — 데이터 바인딩 (공통)
      2. _compile()        — 모델 컴파일 (problem type별 구현)
      3. Gate 3 + Stage 5  — 검증 (공통)
      4. Execute           — solver 실행 (공통)
      5. _convert_result() — 결과 변환 (problem type별 구현)
      6. Post-process      — 저장/로깅 (공통)

    새 problem type 추가 시:
      BaseSolverPipeline을 상속하고 _compile(), _convert_result()만 구현.
      See docs/adding_new_problem_type.md
    """

    def _bind_data(self, math_model: Dict, project_id: str, solver_id: str = "") -> tuple:
        """데이터 바인딩. 성공 시 (bound_data, None), 실패 시 (None, PipelineResult).

        GR-4: catalog 검증 실패(타입/range 위반)가 있으면 L4 진입 차단.
        catalog 미등록 파라미터는 경고만 (새 파라미터일 수 있음).
        """
        try:
            binder = DataBinder(project_id)
            bound_data = binder.bind_all(math_model)
            logger.info(
                f"DataBinder: sets={list(bound_data['set_sizes'].items())}, "
                f"params={len(bound_data['parameters'])}"
            )
            for pw in bound_data.get("parameter_warnings", []):
                logger.warning(f"ParamValidation: {pw}")

            # GR-4: parameter_errors 중 치명적 에러(타입/range 위반) 차단
            param_errors = bound_data.get("parameter_errors", [])
            if param_errors:
                # catalog 미등록은 경고만, 타입/range 위반은 차단
                critical = [e for e in param_errors if "catalog 미등록" not in e]
                warnings = [e for e in param_errors if "catalog 미등록" in e]
                for w in warnings:
                    logger.warning(f"ParamCatalog: {w}")
                if critical:
                    error_msg = "; ".join(critical[:5])
                    logger.error(f"L3→L4 차단: parameter validation failed — {error_msg}")
                    return None, PipelineResult(
                        success=False, phase="bind", solver_id=solver_id,
                        error=f"Parameter validation failed (GR-4): {error_msg}",
                    )

            return bound_data, None
        except Exception as e:
            logger.error(f"DataBinding failed: {e}", exc_info=True)
            return None, PipelineResult(
                success=False, phase="bind", solver_id=solver_id,
                error=f"Data binding failed: {str(e)}"
            )


# ============================================================
# Crew Scheduling Pipeline (SP + IR + Hybrid)
# ============================================================
class SolverPipeline(BaseSolverPipeline):
    """
    crew scheduling용 solver 파이프라인.

    SP 경로: Column Generation → Set Partitioning → solver
    IR 경로: 수학모델 IR → compiler → solver
    Hybrid: CQM → CP-SAT warm start

    GR-1: engine은 domain을 직접 import하지 않음.
    도메인별 generator/converter는 외부에서 주입.

    Usage:
        pipeline = SolverPipeline()
        pipeline.set_domain_adapter(
            generator_factory=lambda trips, params: CrewDutyGenerator(trips, CrewDutyConfig.from_params(params)),
            result_converter=convert_crew_result,
        )
        result = await pipeline.run(...)
    """

    def __init__(self):
        # 도메인 adapter (외부 주입, GR-1)
        self._generator_factory = None   # (tasks, params) -> generator instance
        self._sp_result_converter = None  # convert function

    def set_domain_adapter(
        self,
        generator_factory=None,
        result_converter=None,
    ):
        """도메인별 generator/converter 주입 (GR-1 준수)"""
        if generator_factory:
            self._generator_factory = generator_factory
        if result_converter:
            self._sp_result_converter = result_converter

    # ── Main entry point ───────────────────────────────────

    async def run(
        self,
        math_model: Dict,
        solver_id: str,
        project_id: str,
        solver_name: str = "",
        time_limit_sec: int = 900,
        **kwargs,
    ) -> PipelineResult:
        """전체 파이프라인 실행"""

        ctx = PipelineContext(project_id=project_id)
        logger.info(f"Pipeline: solver={solver_id}, project={project_id}")

        #  Phase 1: Data Binding
        bound_data, bind_error = self._bind_data(math_model, project_id, solver_id)
        if bind_error:
            return bind_error

        #  Phase 2: Compile
        try:
            # ── Set Partitioning 경로 판단: solver × problem_type 2축 ──
            # SP 경로: solver에 SP backend 있음 + problem_type이 Column Generation 사용
            # IR 경로: 그 외 (Column Generation 불가능한 problem type 등)
            from engine.compiler.compiler_registry import supports_set_partitioning
            from engine.config_loader import _resolve_problem_type
            _domain = math_model.get("domain")
            _problem_type = _resolve_problem_type(_domain)
            _use_sp = supports_set_partitioning(solver_id, _problem_type)

            if _use_sp:
                compile_start = time.time()
                compile_result, compile_time = self._compile_set_partitioning(
                    math_model, bound_data, project_id, solver_id, ctx=ctx, **kwargs
                )
            else:
                # ── 기존 경로 (D-Wave 등) ──
                import copy
                _math_model_compiled = copy.deepcopy(math_model)
                _policy_adj = bound_data.get("_policy_adjustments", {})
                if _policy_adj:
                    _var_adj = _policy_adj.get("variable_bounds", {})
                    for _var in _math_model_compiled.get("variables", []):
                        _vid = _var.get("id", "")
                        if _vid in _var_adj:
                            for _field, _val in _var_adj[_vid].items():
                                _old = _var.get(_field)
                                _var[_field] = _val
                                logger.info(f"Policy: {_vid}.{_field} = {_old} → {_val}")
                    _new_big_m = _policy_adj.get("big_m")
                    if _new_big_m:
                        for _p in _math_model_compiled.get("parameters", []):
                            if _p.get("id") == "big_m":
                                _p["default_value"] = _new_big_m
                                _p["value"] = _new_big_m
                        bound_data["parameters"]["big_m"] = _new_big_m
                        logger.info(f"Policy: big_m = {_new_big_m}")

                compiler = get_compiler_unified(solver_id, use_sp=False)
                logger.info(f"Compiler: {type(compiler).__name__}")

                compile_start = time.time()
                compile_result = compiler.compile(_math_model_compiled, bound_data, project_id=project_id, **kwargs)
                compile_time = time.time() - compile_start

            if not compile_result.success:
                return PipelineResult(
                    success=False, phase="compile", solver_id=solver_id,
                    compile_result=compile_result,
                    compile_time_sec=round(compile_time, 3),
                    error=f"Compilation failed: {compile_result.error}"
                )

            logger.info(
                f"Compiled: type={compile_result.solver_type}, "
                f"vars={compile_result.variable_count}, "
                f"constraints={compile_result.constraint_count}, "
                f"time={compile_time:.3f}s"
            )

            if compile_result.warnings:
                for w in compile_result.warnings:
                    logger.warning(f"Compile warning: {w}")

        except ValueError as e:
            return PipelineResult(
                success=False, phase="compile", solver_id=solver_id,
                error=str(e)
            )
        except Exception as e:
            logger.error(f"Compilation exception: {e}", exc_info=True)
            return PipelineResult(
                success=False, phase="compile", solver_id=solver_id,
                error=f"Compilation error: {str(e)}"
            )

        #  Gate 3: Compile Result Validation
        try:
            gate3_input = {
                "variable_count": compile_result.variable_count,
                "constraint_count": compile_result.constraint_count,
                "warnings": compile_result.warnings or [],
                "compile_time": round(compile_time, 3),
            }
            gate3_result = run_gate3(gate3_input, math_model=math_model)
            logger.info(
                f"Gate3: pass={gate3_result['pass']}, "
                f"errors={len(gate3_result['errors'])}, "
                f"warnings={len(gate3_result['warnings'])}, "
                f"stats={gate3_result.get('stats', {})}"
            )

            if not gate3_result["pass"]:
                gate3_errors = "; ".join(gate3_result["errors"])
                logger.error(f"Gate3 BLOCKED execution: {gate3_errors}")
                return PipelineResult(
                    success=False, phase="gate3", solver_id=solver_id,
                    compile_result=compile_result,
                    compile_time_sec=round(compile_time, 3),
                    error=f"Gate3 validation failed: {gate3_errors}"
                )

            # Gate3 결과 저장 (compile_summary에 포함)
            ctx.gate3_result = gate3_result

            if gate3_result["warnings"]:
                for gw in gate3_result["warnings"]:
                    logger.warning(f"Gate3 warning: {gw}")
        except Exception as g3e:
            logger.warning(f"Gate3 check failed (non-blocking): {g3e}")

        # ── Stage 5 validation (presolve) ──
        try:
            from engine.validation.registry import get_registry
            registry = get_registry()

            # Build compile_summary for validators
            total_constraints_in_model = len(math_model.get("constraints", []))
            failed_c = len([w for w in (compile_result.warnings or []) if "all parse methods failed" in str(w).lower()])
            stage5_ctx = {
                "compile_summary": {
                    "variables_created": compile_result.variable_count,
                    "constraints": {
                        "total_in_model": total_constraints_in_model,
                        "applied": total_constraints_in_model - failed_c,
                        "failed": failed_c,
                    },
                    "objective_parsed": True,
                    "warnings": compile_result.warnings or [],
                    "parameter_sources": bound_data.get("parameter_sources", {}),
                    "parameter_warnings": bound_data.get("parameter_warnings", []),
                    "model_type": compile_result.metadata.get("model_type", ""),
                },
                "model_stats": {
                    "total_variables": compile_result.variable_count,
                    "total_constraints": compile_result.constraint_count,
                },
                "math_model": math_model,
                "warnings": compile_result.warnings or [],
                # PresolveProber 전용 context
                "bound_data": bound_data,
                "gate3_result": ctx.gate3_result or {},
                "_compile_result": compile_result,
            }
            stage5_result = registry.run_stage(5, stage5_ctx)
            if stage5_result.items:
                # Store for inclusion in final summary
                ctx.stage5_validation = stage5_result.to_dict()
                logger.info(
                    f"Stage5 validation: errors={stage5_result.error_count}, "
                    f"warnings={stage5_result.warning_count}"
                )

            # ── Presolve Fidelity Enforcement ──
            # PresolveProber가 context에 저장한 결과를 확인하여 실행 차단
            presolve_result = stage5_ctx.get("presolve_result")
            if presolve_result:
                from engine.validation.generic.presolve_models import FidelityDecision
                decision = presolve_result.decision

                if decision == FidelityDecision.HARD_BLOCK:
                    # heuristic solver(CQM 등)는 soft constraint 처리 → presolve hard 검증 부적합
                    # exact solver만 차단, heuristic은 경고로 다운그레이드
                    from engine.compiler.compiler_registry import supports_set_partitioning
                    is_heuristic = not supports_set_partitioning(solver_id)
                    if is_heuristic:
                        logger.warning(
                            f"Presolve HARD_BLOCK downgraded to WARNING for heuristic solver '{solver_id}': "
                            f"{presolve_result.decision_message}"
                        )
                    else:
                        logger.error(
                            f"Presolve HARD_BLOCK: {presolve_result.decision_message}"
                        )
                        return PipelineResult(
                            success=False, phase="presolve", solver_id=solver_id,
                            compile_result=compile_result,
                            compile_time_sec=round(compile_time, 3),
                            error=presolve_result.decision_message,
                            summary={"presolve": presolve_result.to_dict()},
                        )

                # CONDITIONAL_BLOCK / USER_CONFIRMATION → 경고 로그 (차단은 프론트엔드에서)
                if decision in (
                    FidelityDecision.CONDITIONAL_BLOCK,
                    FidelityDecision.USER_CONFIRMATION,
                ):
                    logger.warning(
                        f"Presolve {decision.value}: {presolve_result.decision_message}"
                    )

        except Exception as e:
            logger.warning(f"Stage 5 validation failed: {e}")

        #  Phase 3: Execute
        try:
            executor = get_executor(compile_result.solver_type)

            # 총 시간 예산: generation + compile 시간을 차감하여 solver에 할당
            total_elapsed = compile_time  # compile_time은 generation 포함
            margin = min(60, max(10, time_limit_sec * 0.05))
            solver_time = max(60, time_limit_sec - total_elapsed - margin)
            logger.info(
                f"Executor: {type(executor).__name__}, "
                f"time_budget: total={time_limit_sec}, "
                f"gen+compile={total_elapsed:.0f}, margin={margin:.0f}, "
                f"solver={solver_time:.0f}"
            )

            import asyncio
            execute_result = await asyncio.to_thread(
                executor.execute,
                compile_result,
                time_limit_sec=int(solver_time),
            )

            # ── 디버그: raw solution 구조 확인 ──
            if execute_result.success and execute_result.solution:
                sol = execute_result.solution
                # non-SP: solution["y"] = {"(0,)": 1.0, "(1,)": 1.0, ...}
                # SP: solution["z"] = {"col_id": 1, ...}
                y_data = sol.get("y", {})
                x_data = sol.get("x", {})
                active_y = len(y_data) if isinstance(y_data, dict) else (1 if y_data else 0)
                duties_with_trips = len(set(
                    k.split(",")[-1].strip(" )('\"") if "," in str(k) else str(k)
                    for k in (x_data.keys() if isinstance(x_data, dict) else [])
                ))
                _path_label = "SP" if "z" in sol else "IR"
                logger.info(
                    f"{_path_label} raw solution: top_keys={list(sol.keys())}, "
                    f"active_y={active_y}, "
                    f"active_x_entries={len(x_data) if isinstance(x_data, dict) else 0}, "
                    f"duties_with_trips={duties_with_trips}"
                )

            if not execute_result.success:
                # INFEASIBLE 등 실패 시에도 진단 정보를 summary에 포함
                fail_summary = {
                    "status": execute_result.status,
                    "timing": {
                        "compile_sec": round(compile_time, 3),
                        "execute_sec": execute_result.execution_time_sec,
                        "total_sec": round(compile_time + execute_result.execution_time_sec, 3),
                    },
                    "model_stats": {
                        "total_variables": compile_result.variable_count,
                        "total_constraints": compile_result.constraint_count,
                    },
                    "solver_info": execute_result.solver_info,
                    "infeasibility_info": execute_result.infeasibility_info,
                }
                # user_message가 있으면 에러 메시지에 활용
                user_msg = None
                if execute_result.infeasibility_info:
                    user_msg = execute_result.infeasibility_info.get("user_message")
                error_msg = user_msg or f"Execution failed: {execute_result.error or execute_result.status}"
                return PipelineResult(
                    success=False, phase="execute", solver_id=solver_id,
                    solver_name=solver_name,
                    compile_result=compile_result,
                    compile_time_sec=round(compile_time, 3),
                    execute_result=execute_result,
                    summary=fail_summary,
                    error=error_msg,
                )

            logger.info(
                f"Executed: status={execute_result.status}, "
                f"obj={execute_result.objective_value}, "
                f"time={execute_result.execution_time_sec}s"
            )

        except ValueError as e:
            return PipelineResult(
                success=False, phase="execute", solver_id=solver_id,
                compile_result=compile_result,
                compile_time_sec=round(compile_time, 3),
                error=str(e)
            )
        except Exception as e:
            logger.error(f"Execution exception: {e}", exc_info=True)
            return PipelineResult(
                success=False, phase="execute", solver_id=solver_id,
                compile_result=compile_result,
                compile_time_sec=round(compile_time, 3),
                error=f"Execution error: {str(e)}"
            )

        #  Phase 4: Build Summary 
        summary = self._build_summary(
            math_model, solver_id, solver_name,
            compile_result, compile_time, execute_result,
            bound_data=bound_data, ctx=ctx,
        )

        return PipelineResult(
            success=True,
            phase="complete",
            solver_id=solver_id,
            solver_name=solver_name,
            compile_result=compile_result,
            compile_time_sec=round(compile_time, 3),
            execute_result=execute_result,
            summary=summary,
        )

    def _build_sp_summary(
        self, math_model, solver_id, solver_name,
        compile_result, compile_time, execute_result, bound_data,
        ctx: PipelineContext = None,
    ) -> Dict[str, Any]:
        """Set Partitioning 결과 → 기존 프론트엔드 포맷 summary"""
        from engine.column_generator import load_tasks_from_csv
        from engine.solver_registry import SolverRegistry
        import os

        column_map = ctx.sp_duty_map or {} if ctx else {}
        project_id = ctx.project_id if ctx else ""
        trips_path = os.path.join("uploads", str(project_id), "normalized", "trips.csv")

        tasks = []
        if os.path.exists(trips_path):
            tasks = load_tasks_from_csv(trips_path)

        project_dir = f"uploads/{project_id}" if project_id else None

        # converter 주입: _sp_result_converter가 설정되어 있으면 사용,
        # 없으면 generic fallback (GR-1: engine이 domain을 직접 import 안 함)
        converter_fn = getattr(self, "_sp_result_converter", None)
        if converter_fn is None:
            from engine.sp_result_converter import convert_sp_result
            converter_fn = convert_sp_result

        # params 전달 — crew converter가 제약 기준값에 사용
        _params = bound_data.get("parameters", {}) if bound_data else {}

        # objective_type 추출 (YAML 기반 라벨 해석용)
        from engine.compiler.objective_builder import extract_objective_type
        _obj_type = extract_objective_type(math_model)

        # SP problem의 extra_constraints (side constraint 상태 표시용)
        _extra_constraints = []
        if ctx and ctx.sp_problem and hasattr(ctx.sp_problem, 'extra_constraints'):
            _extra_constraints = ctx.sp_problem.extra_constraints

        interpretation = converter_fn(
            solution=execute_result.solution,
            column_map=column_map,
            tasks=tasks,
            solver_id=solver_id,
            solver_name=solver_name or SolverRegistry.resolve_display_name(solver_id),
            project_dir=project_dir,
            objective_value=execute_result.objective_value,
            params=_params,
            objective_type=_obj_type,
            best_bound=execute_result.best_bound,
            extra_constraints=_extra_constraints,
        )

        # 기존 summary 포맷과 호환
        summary = {
            "solver_id": solver_id,
            "solver_name": solver_name,
            "solver_type": "ortools_cp",
            "status": execute_result.status,
            "objective_value": execute_result.objective_value,
            "model_stats": {
                "total_variables": compile_result.variable_count,
                "total_constraints": compile_result.constraint_count,
                "model_type": "SetPartitioning",
            },
            "timing": {
                "compile_sec": round(compile_time, 3),
                "execute_sec": execute_result.execution_time_sec,
                "total_sec": round(compile_time + execute_result.execution_time_sec, 3),
            },
            "solution": execute_result.solution,
            "interpreted_result": interpretation,
            "compile_summary": {
                "solver_id": solver_id,
                "solver_name": solver_name,
                "solver_type": "ortools_cp",
                "model_type": "SetPartitioning",
                "duty_count": compile_result.metadata.get("duty_count", 0),
                "trip_count": compile_result.metadata.get("trip_count", 0),
                # 프론트엔드 호환 키
                "variables_created": compile_result.variable_count,
                "constraints": {
                    "total_in_model": compile_result.constraint_count,
                    "applied": compile_result.constraint_count,
                    "failed": 0,
                },
                "objective_parsed": True,
                "compile_time_sec": round(compile_time, 3),
                "warnings": [],
                "warning_count": 0,
            },
            "execute_summary": {
                "status": execute_result.status,
                "objective_value": execute_result.objective_value,
                "execute_time_sec": execute_result.execution_time_sec,
            },
            "infeasibility_info": execute_result.infeasibility_info,
        }

        return summary

    # ── Hybrid: CQM → CP-SAT Warm Start ──────────────────────

    async def run_hybrid(
        self,
        math_model: Dict,
        project_id: str,
        time_limit_sec: int = 720,
        solver_name: str = "",
        **kwargs,
    ) -> PipelineResult:
        """CQM → CP-SAT Hybrid 파이프라인.

        1. Data Binding
        2. Column Generation + SP Problem 구축 (공유)
        3. CQM compile + execute (skip_repair=True)
        4. CP-SAT compile + hint 주입 + execute
        5. Summary (hybrid_info 포함)
        """
        from engine.hybrid_strategy import (
            HybridConfig, HybridResult, HybridPhaseResult,
            inject_warmstart_hints, compute_time_budget,
        )
        from engine.compiler.compiler_registry import get_sp_compiler
        from engine.executor import get_executor
        from engine.compiler.base import CompileResult
        import asyncio

        config = HybridConfig.load()
        ctx = PipelineContext(project_id=project_id)
        hybrid_result = HybridResult()
        pipeline_start = time.time()

        logger.info(f"Hybrid pipeline: project={project_id}, mode={config.mode}")

        # ── Phase 1: Data Binding (공통 메서드 재사용) ──
        bound_data, bind_error = self._bind_data(math_model, project_id)
        if bind_error:
            return bind_error

        # ── Phase 2: Column Generation + SP Problem (공유) ──
        sp_problem, all_columns, params, all_task_ids, objective_type = \
            self._prepare_sp_columns(math_model, bound_data, project_id, ctx=ctx)

        if sp_problem is None:
            return PipelineResult(
                success=False, phase="compile",
                error="SP problem construction failed (trips.csv not found?)"
            )

        gen_time = time.time() - pipeline_start

        # ── 시간 예산 계산 ──
        budget = compute_time_budget(time_limit_sec, config, elapsed_sec=gen_time)
        if not budget["viable"]:
            logger.warning("Hybrid: insufficient time, falling back to CP-SAT only")
            from engine.solver_registry import SolverRegistry
            _fb_name = SolverRegistry.resolve_display_name("classical_cpu")
            result = await self.run(
                math_model, "classical_cpu", project_id,
                solver_name=_fb_name,
                time_limit_sec=time_limit_sec, **kwargs,
            )
            # fallback_info 구조화 — 프론트엔드 안내 배너용
            if result.summary:
                result.summary["fallback_info"] = {
                    "occurred": True,
                    "reason": "time_insufficient",
                    "original_strategy": solver_name or "quantum_warmstart",
                    "actual_solver_id": "classical_cpu",
                    "actual_solver_name": _fb_name,
                    "message_ko": f"양자 하이브리드 실행 시간이 부족하여 {_fb_name} 단독으로 전환되었습니다.",
                }
            return result

        # ── Phase 3: CQM Compile + Execute ──
        cqm_success = False
        cqm_solution = None
        try:
            cqm_compiler = get_sp_compiler("dwave_hybrid_cqm")
            cqm_compile = cqm_compiler.compile(
                math_model, bound_data, sp_problem=sp_problem, is_hybrid=True
            )

            if cqm_compile.success:
                cqm_executor = get_executor("dwave_cqm")
                cqm_exec = await asyncio.to_thread(
                    cqm_executor.execute, cqm_compile,
                    time_limit_sec=budget["cqm"],
                    skip_repair=True,
                )
                cqm_solution = cqm_exec.solution
                cqm_selected = sum(
                    1 for v in cqm_solution.get("z", {}).values() if int(v) > 0
                )

                hybrid_result.cqm_phase = HybridPhaseResult(
                    solver="dwave_hybrid_cqm",
                    status=cqm_exec.status,
                    objective_value=cqm_exec.objective_value,
                    time_sec=cqm_exec.execution_time_sec,
                    selected_columns=cqm_selected,
                )
                cqm_success = cqm_exec.success
                logger.info(
                    f"Hybrid CQM phase: {cqm_exec.status}, "
                    f"obj={cqm_exec.objective_value}, "
                    f"selected={cqm_selected}, time={cqm_exec.execution_time_sec}s"
                )
            else:
                logger.warning(f"Hybrid CQM compile failed: {cqm_compile.error}")

        except Exception as e:
            logger.warning(f"Hybrid CQM phase failed: {e}")

        # CQM 실패 → fallback
        if not cqm_success and config.fallback_on_cqm_failure:
            logger.info("Hybrid: CQM failed, falling back to CP-SAT only")
            hybrid_result.strategy_used = "cpsat_fallback"
            remaining = time_limit_sec - (time.time() - pipeline_start)
            from engine.solver_registry import SolverRegistry
            _fb_name = SolverRegistry.resolve_display_name("classical_cpu")
            result = await self.run(
                math_model, "classical_cpu", project_id,
                solver_name=_fb_name,
                time_limit_sec=int(max(remaining, 120)), **kwargs,
            )
            if result.summary:
                result.summary["fallback_info"] = {
                    "occurred": True,
                    "reason": "cqm_failed",
                    "original_strategy": solver_name or "quantum_warmstart",
                    "actual_solver_id": "classical_cpu",
                    "actual_solver_name": _fb_name,
                    "message_ko": f"양자 CQM 솔버 실행 실패로 {_fb_name} 단독으로 전환되었습니다.",
                }
            return result

        # ── Phase 4: CP-SAT Compile + Hint 주입 ──
        cpsat_compiler = get_sp_compiler("classical_cpu")
        cpsat_compile = cpsat_compiler.compile(
            math_model, bound_data, sp_problem=sp_problem
        )

        if not cpsat_compile.success:
            return PipelineResult(
                success=False, phase="compile",
                compile_result=cpsat_compile,
                error=cpsat_compile.error,
            )

        # Hint 주입 (objective_type 기반 hint_policy 적용)
        from engine.compiler.objective_builder import extract_objective_type
        _obj_type = extract_objective_type(math_model)

        hints_injected = 0
        skip_reason = ""
        if cqm_solution and cpsat_compile.solver_model:
            total_duties = int(params.get("total_duties", 0)) or None
            hints_injected, skip_reason = inject_warmstart_hints(
                cpsat_compile.solver_model,
                cpsat_compile.variable_map.get("z", {}),
                cqm_solution,
                config,
                total_duties=total_duties,
                objective_type=_obj_type,
            )
        hybrid_result.hints_injected = hints_injected
        hybrid_result.hints_skipped_reason = skip_reason

        # ── Phase 5: CP-SAT Execute ──
        remaining = time_limit_sec - (time.time() - pipeline_start)
        margin = max(30, time_limit_sec * 0.05)
        solver_time = max(60, int(remaining - margin))

        logger.info(
            f"Hybrid CP-SAT phase: hints={hints_injected}, "
            f"solver_time={solver_time}s"
        )

        executor = get_executor(cpsat_compile.solver_type)
        cpsat_exec = await asyncio.to_thread(
            executor.execute, cpsat_compile, time_limit_sec=solver_time,
        )

        cpsat_selected = sum(
            1 for v in cpsat_exec.solution.get("z", {}).values()
            if isinstance(v, (int, float)) and v > 0
        )
        hybrid_result.cpsat_phase = HybridPhaseResult(
            solver="classical_cpu",
            status=cpsat_exec.status,
            objective_value=cpsat_exec.objective_value,
            time_sec=cpsat_exec.execution_time_sec,
            selected_columns=cpsat_selected,
        )
        hybrid_result.strategy_used = "hybrid_warmstart"

        # improvement: CQM/CP-SAT objective scale이 다르므로 직접 비교 불가
        # 대신 CP-SAT 단독 대비 시간 단축을 기록
        logger.info(
            f"Hybrid complete: CQM={hybrid_result.cqm_phase.status} "
            f"({hybrid_result.cqm_phase.time_sec:.1f}s) → "
            f"CP-SAT={cpsat_exec.status} "
            f"({cpsat_exec.execution_time_sec:.1f}s), "
            f"hints={hints_injected}"
        )

        # ── Phase 6: Summary ──
        compile_time = time.time() - pipeline_start
        summary = self._build_summary(
            math_model, "classical_cpu", solver_name,
            cpsat_compile, compile_time, cpsat_exec,
            bound_data=bound_data, ctx=ctx,
        )
        summary["hybrid_info"] = hybrid_result.to_dict()

        return PipelineResult(
            success=cpsat_exec.status in ("OPTIMAL", "FEASIBLE"),
            phase="complete",
            solver_id="hybrid_cqm_cpsat",
            solver_name=solver_name,
            compile_result=cpsat_compile,
            compile_time_sec=round(compile_time, 3),
            execute_result=cpsat_exec,
            summary=summary,
        )

    def _prepare_sp_columns(
        self, math_model, bound_data, project_id, ctx: PipelineContext = None, **kwargs,
    ):
        """Column generation + Balance cap + SP problem 구축 (solver-agnostic).

        ACG loop으로 column pool을 확장하고, 최종 SP problem을 반환.
        compile은 호출자가 수행 — CQM/CP-SAT 양쪽에서 재사용 가능.

        Returns:
            (sp_problem, all_columns, params, all_task_ids, objective_type)
            또는 (None, ...) — trips.csv 미존재 등 에러 시
        """
        import os

        trips_path = os.path.join("uploads", str(project_id), "normalized", "trips.csv")
        if not os.path.exists(trips_path):
            return None, [], {}, set(), "minimize_duties"

        from engine.column_generator import load_tasks_from_csv, BaseColumnGenerator, BaseColumnConfig
        from engine.compiler.sp_problem import build_sp_problem, GenerationHint

        tasks = load_tasks_from_csv(trips_path)
        logger.info(f"SP: loaded {len(tasks)} tasks from {trips_path}")
        all_task_ids = {t.id for t in tasks}

        params = bound_data.get("parameters", {})
        # domain 정보를 params에 주입 (side constraint pipeline에서 사용)
        params["_domain"] = math_model.get("domain")

        from engine.compiler.objective_builder import extract_objective_type
        objective_type = extract_objective_type(math_model)

        # ── Solver 입력 캐시: Column Gen 결과 재사용 ──
        # solver 변경 재실행 시 Column Gen 스킵 (30초 → 즉시)
        reuse_pool = kwargs.get("reuse_pool", True)  # 기본: 캐시 재사용
        if reuse_pool:
            from engine.cache.solver_input_cache import (
                FileSolverInputCache, build_cache_key, hash_params, CacheMetadata
            )
            from engine.config_loader import _resolve_problem_type

            _cache = FileSolverInputCache()
            _problem_type = _resolve_problem_type(params.get("_domain"))
            _cache_key = build_cache_key(
                _problem_type,
                model_version_id=kwargs.get("model_version_id"),
                data_version_id=kwargs.get("data_version_id"),
                params_hash=hash_params(params),
            )

            cached = _cache.load(project_id, _cache_key)
            if cached is not None:
                logger.info(f"SP: Column Gen SKIPPED — cache hit (key={_cache_key})")
                _all_columns = cached["all_columns"]
                _all_task_ids_cached = cached["all_task_ids"]
                _objective_type = cached["objective_type"]
                _params = cached["params"]
                # params에 _domain 재주입 (캐시에서 제외했을 수 있음)
                _params["_domain"] = params.get("_domain")

                # SP problem은 캐시에 포함하지 않음 (extra_constraints는 compile 시점 재생성)
                sp_problem = build_sp_problem(
                    _all_columns, _params, _all_task_ids_cached, _objective_type
                )
                if ctx:
                    ctx.sp_duties = _all_columns
                    ctx.sp_duty_map = {d.id: d for d in _all_columns}
                    ctx.sp_problem = sp_problem
                return sp_problem, _all_columns, _params, _all_task_ids_cached, _objective_type

        enable_acg = os.environ.get("ENABLE_ADAPTIVE_CG", "true").lower() == "true"
        max_attempts = int(os.environ.get("ACG_MAX_ATTEMPTS", "3")) if enable_acg else 1

        logger.info(
            f"SP: objective={objective_type}, "
            f"total_duties={params.get('total_duties')}, "
            f"day_crew_count={params.get('day_crew_count')}, "
            f"night_crew_count={params.get('night_crew_count')}"
        )

        all_columns = []
        hint = None
        sp_problem = None

        for attempt in range(1, max_attempts + 1):
            acg_scale = 1.0 + (attempt - 1) * 0.5  # 1.0 → 1.5 → 2.0

            # Generator (에스컬레이션된 config + hint 적용)
            if self._generator_factory:
                gen = self._generator_factory(tasks, params)
            else:
                config = BaseColumnConfig.from_params(params)
                gen = BaseColumnGenerator(tasks, config)
            gen.config.acg_scale = acg_scale

            # Layer 2: hint 적용
            if hint:
                if hint.prefer_longer:
                    gen.config.min_column_depth = max(
                        gen.config.min_column_depth,
                        int(hint.min_tasks_per_column * 0.8),
                    )

                # Seed-based diversification
                if hint.seed_trips:
                    gen.config.seed_trips = hint.seed_trips

                logger.info(
                    f"ACG hint applied: min_depth={gen.config.min_column_depth}, "
                    f"seed_trips={len(hint.seed_trips) if hint.seed_trips else 0}, "
                    f"prefer_longer={hint.prefer_longer}"
                )

            # Pair-frequency penalty: 기존 pool에서 trip-pair 빈도 계산
            if all_columns and attempt > 1:
                from collections import Counter as _Counter
                pf_start = time.time()
                pf = _Counter()
                for col in all_columns:
                    trips = sorted(col.trips)
                    for i in range(len(trips)):
                        for j in range(i + 1, len(trips)):
                            pf[(trips[i], trips[j])] += 1
                gen.config.pair_frequency = dict(pf)
                gen.config.pair_frequency_max = max(pf.values()) if pf else 1
                pf_elapsed = time.time() - pf_start
                logger.info(
                    f"ACG pair-frequency: {len(pf)} unique pairs, "
                    f"max_freq={gen.config.pair_frequency_max}, "
                    f"from {len(all_columns)} columns ({pf_elapsed:.2f}s)"
                )

            new_columns = gen.generate()

            # Column pool 누적 (이전 attempt 결과 보존)
            if attempt == 1:
                all_columns = new_columns
            else:
                max_id = max((c.id for c in all_columns), default=0)
                for c in new_columns:
                    max_id += 1
                    c.id = max_id
                    all_columns.append(c)
                all_columns = gen._remove_dominated(all_columns)

            # pre-cap coverage 기록 (ACG 판단용)
            pre_cap_coverage = set(tid for c in all_columns for tid in c.trips)

            # balance_workload: type별 column cap (rhs 비례 — coverage 보호 포함)
            if objective_type == "balance_workload" and len(all_columns) > 50000:
                from engine.compiler.sp_problem import ColumnType
                day_rhs = int(params.get("day_crew_count", 32))
                night_rhs = int(params.get("night_crew_count", 13))
                cap_per_rhs = int(os.environ.get("BALANCE_CAP_PER_RHS", "1500"))
                day_cap = day_rhs * cap_per_rhs
                night_cap = night_rhs * cap_per_rhs

                # Step 1: type별 분류
                day_all = [c for c in all_columns if c.column_type in ColumnType.DAY_GROUP]
                night_all = [c for c in all_columns if c.column_type in ColumnType.NIGHT_GROUP]

                # Step 2: Coverage 보호 — 한쪽 type에만 존재하는 task의 column 보장
                task_in_day: dict = {}
                task_in_night: dict = {}
                for c in day_all:
                    for tid in c.trips:
                        task_in_day.setdefault(tid, []).append(c)
                for c in night_all:
                    for tid in c.trips:
                        task_in_night.setdefault(tid, []).append(c)

                protected_day_ids: set = set()
                protected_night_ids: set = set()
                all_covered = set(task_in_day.keys()) | set(task_in_night.keys())

                for tid in all_covered:
                    in_day = task_in_day.get(tid, [])
                    in_night = task_in_night.get(tid, [])
                    if in_day and not in_night:
                        # day에만 존재 → cost 최소 column 보호
                        protected_day_ids.add(min(in_day, key=lambda c: c.cost).id)
                    elif in_night and not in_day:
                        # night에만 존재 → cost 최소 column 보호
                        protected_night_ids.add(min(in_night, key=lambda c: c.cost).id)

                # Step 3: Day — 보호 column 우선 + 나머지 cost 순 cap
                day_protected = [c for c in day_all if c.id in protected_day_ids]
                day_rest = sorted(
                    [c for c in day_all if c.id not in protected_day_ids],
                    key=lambda c: c.cost
                )
                day_cols = (day_protected + day_rest)[:max(day_cap, len(day_protected))]

                # Step 3b: Night — subtype 다양성 보존
                # overnight은 새벽 trip의 유일한 커버 수단
                # → night_cap 내에서 전량 확보, 나머지 슬롯을 night으로 채움
                night_pure = sorted(
                    [c for c in night_all if c.column_type == ColumnType.NIGHT],
                    key=lambda c: c.cost
                )
                overnight_sorted = sorted(
                    [c for c in night_all if c.column_type == ColumnType.OVERNIGHT],
                    key=lambda c: c.cost
                )

                # subtype 전량 확보 (night_cap << 총량이므로 여유 충분)
                reserved_count = len(overnight_sorted)
                remaining_night_cap = max(0, night_cap - reserved_count)
                night_capped = night_pure[:remaining_night_cap]

                # coverage 보호 column 병합 (night_pure에서 잘린 것 중 보호 대상)
                night_capped_ids = {c.id for c in night_capped}
                for c in night_pure:
                    if c.id in protected_night_ids and c.id not in night_capped_ids:
                        night_capped.append(c)

                night_cols = overnight_sorted + night_capped

                before_cap = len(all_columns)
                all_columns = day_cols + night_cols

                # Step 4: 최종 coverage 검증 (defensive)
                post_cap_tasks = set(tid for c in all_columns for tid in c.trips)
                lost_tasks = all_covered - post_cap_tasks
                if lost_tasks:
                    logger.warning(
                        f"Balance cap lost {len(lost_tasks)} tasks despite protection: "
                        f"{sorted(lost_tasks)[:10]}"
                    )
                    restore_pool = day_rest + [c for c in night_pure if c.id not in night_capped_ids]
                    restored = 0
                    restored_ids: set = set()
                    for tid in lost_tasks:
                        for c in restore_pool:
                            if tid in c.trips and c.id not in restored_ids:
                                all_columns.append(c)
                                restored_ids.add(c.id)
                                restored += 1
                                break
                    if restored:
                        logger.info(f"Balance cap: restored {restored} columns for lost tasks")

                logger.info(
                    f"Balance column cap: {before_cap} -> {len(all_columns)} "
                    f"(day={len(day_cols)}/{day_cap}, "
                    f"night_pure={len(night_capped)}, "
                    f"overnight={len(overnight_sorted)}, "
                    f"night_total={len(night_cols)}/{night_cap}, "
                    f"cap_per_rhs={cap_per_rhs}, "
                    f"protected: day={len(protected_day_ids)}, night={len(protected_night_ids)})"
                )

            # SP Problem 구축
            sp_problem = build_sp_problem(all_columns, params, all_task_ids, objective_type)

            # Layer 1: Coverage capacity 진단
            # balance_workload: top-K 기반 (전체 avg 낮아도 top-K가 충분하면 feasible)
            use_top_k = (objective_type == "balance_workload")
            cov_diag = sp_problem.diagnose_coverage(use_top_k=use_top_k)
            type_dist = sp_problem.diagnostics.get("column_type_distribution", {})

            post_cap_cov = len(set(tid for c in all_columns for tid in c.trips))
            logger.info(
                f"ACG attempt {attempt}/{max_attempts}: "
                f"scale={acg_scale}, generated={len(new_columns)}, "
                f"total_pool={len(all_columns)}, "
                f"types={type_dist}, "
                f"uncovered={len(sp_problem.uncovered_tasks)}, "
                f"degree_1={len(sp_problem.degree_1_tasks)}, "
                f"coverage: pre_cap={len(pre_cap_coverage)}/{len(all_task_ids)}, "
                f"post_cap={post_cap_cov}/{len(all_task_ids)}, "
                f"required_avg={cov_diag.required_avg:.1f}, "
                f"current_avg={cov_diag.current_avg:.1f}, "
                f"capacity_gap={cov_diag.capacity_gap}, "
                f"feasible={cov_diag.feasible}"
            )

            # Pre-solve: 재생성이 의미있는 경우만 계속
            # cap-caused uncovered 감지: generator는 커버했는데 cap에서 잘린 경우 skip
            if sp_problem.uncovered_tasks and pre_cap_coverage >= all_task_ids:
                logger.warning(
                    f"ACG attempt {attempt}: uncovered={len(sp_problem.uncovered_tasks)} "
                    f"caused by balance cap (generator had full coverage). "
                    f"Skipping regeneration — cap 보호 로직으로 해결 필요."
                )
            elif sp_problem.should_regenerate(params, use_top_k=use_top_k) and attempt < max_attempts:
                # Bottleneck trip 식별: coverage가 하위 10%인 trip
                bottleneck = []
                if sp_problem.task_to_columns:
                    trip_coverage = [
                        (tid, len(sp_problem.task_to_columns.get(tid, [])))
                        for tid in sp_problem.task_ids
                    ]
                    trip_coverage.sort(key=lambda x: x[1])
                    # 하위 10% (최소 10개, 최대 50개)
                    cutoff_idx = max(10, len(trip_coverage) // 10)
                    bottleneck = [tid for tid, _ in trip_coverage[:min(cutoff_idx, 50)]]

                # Layer 2: 진단 → 힌트 생성 (다음 attempt에서 사용)
                hint = GenerationHint.from_diagnostics(
                    cov_diag, bottleneck_trips=bottleneck if bottleneck else None
                )
                logger.warning(
                    f"ACG attempt {attempt}: pool 부족 → 재생성 "
                    f"(gap={cov_diag.capacity_gap}, bottleneck_trips={len(bottleneck)}, "
                    f"hint: prefer_longer={hint.prefer_longer}, "
                    f"min_tasks={hint.min_tasks_per_column:.1f})"
                )
                continue

            # ACG loop에서 SP problem 구축 완료 — compile 준비 완료
            sp_problem = sp_problem  # 현재 attempt의 최종 SP problem
            break  # compile은 호출자가 수행

        # column_map + sp_problem 저장
        if ctx:
            ctx.sp_duties = all_columns
            ctx.sp_duty_map = {d.id: d for d in all_columns}
            ctx.sp_problem = sp_problem

        # ── 캐시 저장: Column Gen 결과 ──
        try:
            from engine.cache.solver_input_cache import (
                FileSolverInputCache, build_cache_key, hash_params, CacheMetadata
            )
            from engine.config_loader import _resolve_problem_type
            import time as _time

            _cache = FileSolverInputCache()
            _problem_type = _resolve_problem_type(params.get("_domain"))
            _cache_key = build_cache_key(
                _problem_type,
                model_version_id=kwargs.get("model_version_id"),
                data_version_id=kwargs.get("data_version_id"),
                params_hash=hash_params(params),
            )
            _payload = {
                "all_columns": all_columns,
                "all_task_ids": all_task_ids,
                "objective_type": objective_type,
                "params": {k: v for k, v in params.items() if k != "_task_map"},
            }
            _meta = CacheMetadata(
                cache_key=_cache_key,
                problem_type=_problem_type,
                created_at=_time.time(),
                payload_type="sp_columns",
            )
            _cache.save(project_id, _cache_key, _payload, _meta)
        except Exception as _ce:
            logger.warning(f"SP cache save failed (non-blocking): {_ce}")

        return sp_problem, all_columns, params, all_task_ids, objective_type

    def _compile_set_partitioning(
        self, math_model, bound_data, project_id, solver_id, ctx: PipelineContext = None, **kwargs
    ):
        """
        Set Partitioning 컴파일 (기존 단독 경로).

        _prepare_sp_columns()로 SP problem 구축 후 지정 solver로 compile.

        Returns:
            (CompileResult, compile_time_sec)
        """
        import os
        compile_start = time.time()

        from engine.compiler.compiler_registry import get_sp_compiler
        from engine.compiler.sp_problem import build_sp_problem

        sp_problem, all_columns, params, all_task_ids, objective_type = \
            self._prepare_sp_columns(math_model, bound_data, project_id, ctx=ctx, **kwargs)

        if sp_problem is None:
            from engine.compiler.base import CompileResult
            trips_path = os.path.join("uploads", str(project_id), "normalized", "trips.csv")
            return CompileResult(
                success=False, error=f"trips.csv not found: {trips_path}"
            ), 0.0

        # SP 컴파일
        compiler = get_sp_compiler(solver_id)
        compile_result = compiler.compile(
            math_model, bound_data, sp_problem=sp_problem
        )

        if not compile_result.success:
            # constraint 완화 시도
            cov_diag = sp_problem.diagnose_coverage(
                use_top_k=(objective_type == "balance_workload")
            )
            if cov_diag.capacity_gap > 0:
                suggested_total = cov_diag.max_columns + max(
                    2, cov_diag.capacity_gap // int(cov_diag.current_avg or 7)
                )
                logger.warning(
                    f"ACG: compile failed with capacity_gap={cov_diag.capacity_gap}. "
                    f"Relaxing total_columns: =={cov_diag.max_columns} → <={suggested_total}"
                )
                relaxed_params = dict(params)
                relaxed_params["total_duties"] = suggested_total
                if params.get("day_crew_count") and params.get("night_crew_count"):
                    ratio = suggested_total / max(cov_diag.max_columns, 1)
                    relaxed_params["day_crew_count"] = int(
                        float(params["day_crew_count"]) * ratio
                    )
                    relaxed_params["night_crew_count"] = (
                        suggested_total - relaxed_params["day_crew_count"]
                    )

                relaxed_problem = build_sp_problem(
                    all_columns, relaxed_params, all_task_ids, objective_type
                )
                relaxed_diag = relaxed_problem.diagnose_coverage()
                if relaxed_diag.feasible:
                    compile_result = compiler.compile(
                        math_model,
                        {**bound_data, "parameters": relaxed_params},
                        sp_problem=relaxed_problem,
                    )
                    if compile_result.success:
                        compile_result.warnings = compile_result.warnings or []
                        compile_result.warnings.append(
                            f"partition_count_relaxed: exact {cov_diag.max_columns} infeasible, "
                            f"relaxed to {suggested_total}"
                        )

        compile_time = time.time() - compile_start
        return compile_result, round(compile_time, 3)

    def _build_summary(
        self,
        math_model: Dict,
        solver_id: str,
        solver_name: str,
        compile_result: CompileResult,
        compile_time: float,
        execute_result: ExecuteResult,
        bound_data: Optional[Dict] = None,
        ctx: PipelineContext = None,
    ) -> Dict[str, Any]:
        """프론트엔드에 보낼 결과 요약 생성 (compile_summary 포함)"""

        # ── SP 결과 변환 (Set Partitioning 경로) ──
        if compile_result.metadata.get("model_type") == "SetPartitioning":
            return self._build_sp_summary(
                math_model, solver_id, solver_name,
                compile_result, compile_time, execute_result, bound_data, ctx=ctx,
            )

        # 비영 솔루션 변수 개수
        nonzero = 0
        for vid, val in execute_result.solution.items():
            if isinstance(val, dict):
                nonzero += len(val)
            elif val != 0:
                nonzero += 1

        # ── compile_summary 구조화 ──
        warnings_list = compile_result.warnings or []
        total_constraints_in_model = len(math_model.get("constraints", []))
        failed_constraints = len([w for w in warnings_list if "could not parse" in w.lower()])
        applied_constraints = total_constraints_in_model - failed_constraints

        # 목적함수 파싱 여부 확인
        objective_parsed = True
        for w in warnings_list:
            if "objective" in w.lower() and ("could not parse" in w.lower() or "default" in w.lower()):
                objective_parsed = False
                break

        compile_summary = {
            "solver_id": solver_id,
            "solver_name": solver_name,
            "solver_type": compile_result.solver_type,
            "variables_created": compile_result.variable_count,
            "constraints": {
                "total_in_model": total_constraints_in_model,
                "applied": applied_constraints,
                "failed": failed_constraints,
            },
            "objective_parsed": objective_parsed,
            "compile_time_sec": round(compile_time, 3),
            "warnings": warnings_list,
            "warning_count": len(warnings_list),
            "parameter_sources": (bound_data or {}).get("parameter_sources", {}),
            "parameter_warnings": (bound_data or {}).get("parameter_warnings", []),
        }

        # Gate3 결과 포함
        gate3 = ctx.gate3_result if ctx else None
        if gate3:
            compile_summary["gate3"] = {
                "pass": gate3["pass"],
                "errors": gate3.get("errors", []),
                "warnings": gate3.get("warnings", []),
                "stats": gate3.get("stats", {}),
            }

        # ── execute_summary 구조화 ──
        execute_summary = {
            "status": execute_result.status,
            "objective_value": execute_result.objective_value,
            "best_bound": execute_result.best_bound,
            "execute_time_sec": execute_result.execution_time_sec,
            "nonzero_variables": nonzero,
            "solver_info": execute_result.solver_info,
        }

        summary = {
            "solver_id": solver_id,
            "solver_name": solver_name,
            "solver_type": compile_result.solver_type,
            "status": execute_result.status,
            "objective_value": execute_result.objective_value,
            "best_bound": execute_result.best_bound,

            "model_stats": {
                "total_variables": compile_result.variable_count,
                "total_constraints": compile_result.constraint_count,
                "nonzero_variables": nonzero,
            },

            "timing": {
                "compile_sec": round(compile_time, 3),
                "execute_sec": execute_result.execution_time_sec,
                "total_sec": round(compile_time + execute_result.execution_time_sec, 3),
            },

            "solver_info": execute_result.solver_info,
            "solution": execute_result.solution,

            # INFEASIBLE 진단 정보 (executor가 생성한 conflict_hints 등)
            "infeasibility_info": execute_result.infeasibility_info,

            # 구조화된 리포트 데이터
            "compile_summary": compile_summary,
            "execute_summary": execute_summary,

            # 하위 호환 유지
            "compile_warnings": warnings_list,

            # Policy snapshot (single resolve, downstream reuse)
            "policy_snapshot": bound_data.get("_policy_result"),
        }


        # ── 결과 해석 및 산출물 저장 ──
        try:
            project_dir = f"uploads/{ctx.project_id}" if ctx and ctx.project_id else None
            if project_dir and os.path.isdir(project_dir):
                interpreted = interpret_result(
                    solution=execute_result.solution,
                    math_model=math_model,
                    project_dir=project_dir,
                    solver_id=solver_id,
                    solver_name=solver_name,
                    status=execute_result.status,
                    objective_value=execute_result.objective_value,
                    params=(bound_data or {}).get("parameters"),
                    policy_snapshot=bound_data.get("_policy_result"),
                )
                saved = save_artifacts(
                    project_dir, execute_result.solution, interpreted, solver_id,
                    domain=math_model.get("domain", "general"),
                )
                summary["interpreted_result"] = interpreted
                summary["artifacts"] = {k: str(v) for k, v in saved.items()}
                logger.info(f"Result interpreted: {interpreted['objective_label']}, artifacts={list(saved.keys())}")
        except Exception as e:
            logger.warning(f"Result interpretation failed: {e}")

        # ── Stage 6 validation (post-solve) ──
        try:
            from engine.validation.registry import get_registry
            registry = get_registry()
            stage6_ctx = {
                "status": execute_result.status,
                "objective_value": execute_result.objective_value,
                "best_bound": execute_result.best_bound,
                "solution": execute_result.solution,
                "math_model": math_model,
                "interpreted_result": summary.get("interpreted_result", {}),
                "execution_time_sec": execute_result.execution_time_sec,
                "compile_summary": summary.get("compile_summary", {}),
                "domain": math_model.get("domain", ""),
                "parameters": math_model.get("parameters", {}),
                "infeasibility_info": execute_result.infeasibility_info,
            }
            stage6_result = registry.run_stage(6, stage6_ctx)
            validation = stage6_result.to_dict()

            # Merge Stage 5 presolve findings into the validation response
            # 솔버 실행 성공 시 presolve error를 info로 다운그레이드
            stage5 = ctx.stage5_validation if ctx else None
            solve_success = execute_result.status in ("OPTIMAL", "FEASIBLE")
            if stage5 and stage5.get("items"):
                merged_items = []
                downgraded = 0
                for item in stage5["items"]:
                    if solve_success and item.get("severity") == "error":
                        # 솔버가 성공했으므로 presolve error는 참고 정보로 다운그레이드
                        item = dict(item)
                        item["severity"] = "info"
                        item["message"] = (
                            "[Presolve 참고] " + item.get("message", "")
                        )
                        downgraded += 1
                    merged_items.append(item)

                if downgraded:
                    logger.info(
                        f"Presolve: {downgraded} error(s) downgraded to info "
                        f"(solver status={execute_result.status})"
                    )

                # 다운그레이드 후 severity별 카운트 재계산
                extra_errors = sum(1 for it in merged_items if it.get("severity") == "error")
                extra_warnings = sum(1 for it in merged_items if it.get("severity") == "warning")
                extra_infos = sum(1 for it in merged_items if it.get("severity") == "info")

                validation["items"] = merged_items + validation["items"]
                validation["error_count"] += extra_errors
                validation["warning_count"] += extra_warnings
                validation["info_count"] += extra_infos
                validation["validators_run"] = stage5.get("validators_run", []) + validation["validators_run"]
                if not solve_success and stage5.get("blocking"):
                    validation["blocking"] = True
                    validation["passed"] = False

            summary["validation"] = validation
        except Exception as e:
            logger.warning(f"Post-solve validation failed: {e}")

        return summary

