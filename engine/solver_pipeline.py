from __future__ import annotations
import os
# engine/solver_pipeline.py
# ============================================================
# Solver Pipeline: 수학 모델 IR -> 컴파일 -> 실행 -> 결과 통합
# ============================================================


import logging
import time
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


# ============================================================
# Pipeline Runner
# ============================================================
class SolverPipeline:
    """
    수학 모델 IR을 선택된 솔버로 컴파일하고 실행하는 파이프라인.

    GR-1: engine은 domain을 직접 import하지 않음.
    도메인별 generator/converter는 외부에서 주입.

    Usage:
        pipeline = SolverPipeline()
        # 도메인 adapter 주입 (agent/app 계층에서)
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

        self._current_project_id = project_id
        logger.info(f"Pipeline: solver={solver_id}, project={project_id}")

        #  Phase 1: Data Binding 
        try:
            binder = DataBinder(project_id)
            bound_data = binder.bind_all(math_model)
            logger.info(
                f"DataBinder: sets={list(bound_data['set_sizes'].items())}, "
                f"params={len(bound_data['parameters'])}"
            )
            # F8: log parameter warnings from validation
            for pw in bound_data.get("parameter_warnings", []):
                logger.warning(f"ParamValidation: {pw}")
        except Exception as e:
            logger.error(f"DataBinding failed: {e}", exc_info=True)
            return PipelineResult(
                success=False, phase="bind", solver_id=solver_id,
                error=f"Data binding failed: {str(e)}"
            )

        #  Phase 2: Compile
        try:
            # ── Set Partitioning 경로 판단 (SolverInfo 기반 — 하드코딩 제거) ──
            # crew scheduling 등 assignment 문제는 SP가 구조적으로 올바름.
            # DutyGenerator가 시간 검증 전부 수행 → solver는 coverage만 결정.
            from engine.compiler.compiler_registry import supports_set_partitioning
            _use_sp = supports_set_partitioning(solver_id)

            if _use_sp:
                compile_start = time.time()
                compile_result, compile_time = self._compile_set_partitioning(
                    math_model, bound_data, project_id, solver_id, **kwargs
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
            self._gate3_result = gate3_result

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
                "gate3_result": getattr(self, "_gate3_result", {}),
                "_compile_result": compile_result,
            }
            stage5_result = registry.run_stage(5, stage5_ctx)
            if stage5_result.items:
                # Store for inclusion in final summary
                self._stage5_validation = stage5_result.to_dict()
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
            logger.info(f"Executor: {type(executor).__name__}")

            import asyncio
            execute_result = await asyncio.to_thread(
                executor.execute,
                compile_result,
                time_limit_sec=time_limit_sec,
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
            bound_data=bound_data,
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
    ) -> Dict[str, Any]:
        """Set Partitioning 결과 → 기존 프론트엔드 포맷 summary"""
        from engine.column_generator import load_tasks_from_csv
        import os

        column_map = getattr(self, "_sp_duty_map", {})
        project_id = getattr(self, "_current_project_id", "")
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
        interpretation = converter_fn(
            solution=execute_result.solution,
            column_map=column_map,
            tasks=tasks,
            solver_id=solver_id,
            solver_name=solver_name or "CP-SAT (Set Partitioning)",
            project_dir=project_dir,
            objective_value=execute_result.objective_value,
            params=_params,
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

    def _compile_set_partitioning(
        self, math_model, bound_data, project_id, solver_id, **kwargs
    ):
        """
        Set Partitioning 컴파일 (Adaptive Column Generation 지원).

        ENABLE_ADAPTIVE_CG=true일 때: 최대 N회 반복하며 column pool 확장.
        ENABLE_ADAPTIVE_CG=false일 때: 기존 단일 실행.

        Returns:
            (CompileResult, compile_time_sec)
        """
        import os
        compile_start = time.time()

        # Trip 로딩
        trips_path = os.path.join("uploads", str(project_id), "normalized", "trips.csv")
        if not os.path.exists(trips_path):
            from engine.compiler.base import CompileResult
            return CompileResult(
                success=False, error=f"trips.csv not found: {trips_path}"
            ), 0.0

        from engine.column_generator import load_tasks_from_csv, BaseColumnGenerator, BaseColumnConfig
        from engine.compiler.compiler_registry import get_sp_compiler
        from engine.compiler.sp_problem import build_sp_problem

        tasks = load_tasks_from_csv(trips_path)
        logger.info(f"SP: loaded {len(tasks)} tasks from {trips_path}")
        all_task_ids = {t.id for t in tasks}

        params = bound_data.get("parameters", {})

        # ACG 설정 (feature flag)
        enable_acg = os.environ.get("ENABLE_ADAPTIVE_CG", "true").lower() == "true"
        max_attempts = int(os.environ.get("ACG_MAX_ATTEMPTS", "3")) if enable_acg else 1

        # params 전달 확인 로그 (50 duties 버그 추적용)
        logger.info(
            f"SP params: total_duties={params.get('total_duties')}, "
            f"day_crew_count={params.get('day_crew_count')}, "
            f"night_crew_count={params.get('night_crew_count')}"
        )

        from engine.compiler.sp_problem import GenerationHint

        all_columns = []
        best_compile = None
        hint = None  # Layer 2: 진단 결과 → generator 힌트

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
                pf_elapsed = time.time() - pf_start
                logger.info(
                    f"ACG pair-frequency: {len(pf)} unique pairs "
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

            # SP Problem 구축
            sp_problem = build_sp_problem(all_columns, params, all_task_ids)

            # Layer 1: Coverage capacity 진단
            cov_diag = sp_problem.diagnose_coverage()
            type_dist = sp_problem.diagnostics.get("column_type_distribution", {})

            logger.info(
                f"ACG attempt {attempt}/{max_attempts}: "
                f"scale={acg_scale}, generated={len(new_columns)}, "
                f"total_pool={len(all_columns)}, "
                f"types={type_dist}, "
                f"uncovered={len(sp_problem.uncovered_tasks)}, "
                f"degree_1={len(sp_problem.degree_1_tasks)}, "
                f"coverage: required_avg={cov_diag.required_avg:.1f}, "
                f"current_avg={cov_diag.current_avg:.1f}, "
                f"capacity_gap={cov_diag.capacity_gap}, "
                f"feasible={cov_diag.feasible}"
            )

            # Pre-solve: 재생성이 의미있는 경우만 계속
            if sp_problem.should_regenerate(params) and attempt < max_attempts:
                # Bottleneck trip 식별: coverage density가 낮은 trip
                bottleneck = []
                if sp_problem.task_to_columns:
                    for tid in sp_problem.task_ids:
                        col_count = len(sp_problem.task_to_columns.get(tid, []))
                        if col_count <= 3:
                            bottleneck.append(tid)
                    # 너무 많으면 상위 50개만
                    bottleneck = bottleneck[:50]

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

            # SP 컴파일
            compiler = get_sp_compiler(solver_id)
            compile_result = compiler.compile(
                math_model, bound_data, sp_problem=sp_problem
            )

            if compile_result.success:
                best_compile = compile_result
                break

            # 컴파일 실패 → Layer 3: constraint 완화 시도
            if not compile_result.success and cov_diag.capacity_gap > 0:
                # == 제약을 <= 로 완화 (추가 slack 허용)
                suggested_total = cov_diag.max_columns + max(
                    2, cov_diag.capacity_gap // int(cov_diag.current_avg or 7)
                )
                logger.warning(
                    f"ACG: compile failed with capacity_gap={cov_diag.capacity_gap}. "
                    f"Relaxing total_columns: =={cov_diag.max_columns} → <={suggested_total}"
                )
                relaxed_params = dict(params)
                relaxed_params["total_duties"] = suggested_total
                # day/night도 비례 완화
                if params.get("day_crew_count") and params.get("night_crew_count"):
                    ratio = suggested_total / max(cov_diag.max_columns, 1)
                    relaxed_params["day_crew_count"] = int(
                        float(params["day_crew_count"]) * ratio
                    )
                    relaxed_params["night_crew_count"] = (
                        suggested_total - relaxed_params["day_crew_count"]
                    )
                    logger.info(
                        f"ACG relaxed: total={suggested_total}, "
                        f"day={relaxed_params['day_crew_count']}, "
                        f"night={relaxed_params['night_crew_count']}"
                    )

                # 완화된 params로 SP problem 재구축 + 재진단 + 재컴파일
                relaxed_problem = build_sp_problem(
                    all_columns, relaxed_params, all_task_ids
                )
                # 완화 후 재진단 (night column이 충분한지)
                relaxed_diag = relaxed_problem.diagnose_coverage()
                if not relaxed_diag.feasible:
                    logger.warning(
                        f"ACG: relaxation도 infeasible "
                        f"(gap={relaxed_diag.capacity_gap}), 추가 완화 필요"
                    )
                else:
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
                        best_compile = compile_result
                        break

            best_compile = compile_result
            if attempt < max_attempts:
                hint = GenerationHint.from_diagnostics(cov_diag)
                logger.warning(f"ACG attempt {attempt}: compile failed, retrying")
                continue
            break

        compile_time = time.time() - compile_start

        # column_map 저장
        if best_compile and best_compile.success:
            self._sp_duties = all_columns
            self._sp_duty_map = {d.id: d for d in all_columns}

        return best_compile or compile_result, round(compile_time, 3)

    def _build_summary(
        self,
        math_model: Dict,
        solver_id: str,
        solver_name: str,
        compile_result: CompileResult,
        compile_time: float,
        execute_result: ExecuteResult,
        bound_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """프론트엔드에 보낼 결과 요약 생성 (compile_summary 포함)"""

        # ── SP 결과 변환 (Set Partitioning 경로) ──
        if compile_result.metadata.get("model_type") == "SetPartitioning":
            return self._build_sp_summary(
                math_model, solver_id, solver_name,
                compile_result, compile_time, execute_result, bound_data,
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
        gate3 = getattr(self, "_gate3_result", None)
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
            project_dir = f"uploads/{self._current_project_id}" if hasattr(self, '_current_project_id') else None
            if project_dir and os.path.isdir(project_dir):
                interpreted = interpret_result(
                    solution=execute_result.solution,
                    math_model=math_model,
                    project_dir=project_dir,
                    solver_id=solver_id,
                    solver_name=solver_name,
                    status=execute_result.status,
                    objective_value=execute_result.objective_value,
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
            stage5 = getattr(self, "_stage5_validation", None)
            if stage5 and stage5.get("items"):
                validation["items"] = stage5["items"] + validation["items"]
                validation["error_count"] += stage5.get("error_count", 0)
                validation["warning_count"] += stage5.get("warning_count", 0)
                validation["info_count"] += stage5.get("info_count", 0)
                validation["validators_run"] = stage5.get("validators_run", []) + validation["validators_run"]
                if stage5.get("blocking"):
                    validation["blocking"] = True
                    validation["passed"] = False

            summary["validation"] = validation
        except Exception as e:
            logger.warning(f"Post-solve validation failed: {e}")

        return summary

