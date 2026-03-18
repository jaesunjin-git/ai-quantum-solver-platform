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

from engine.compiler import get_compiler
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

    Usage:
        pipeline = SolverPipeline()
        result = await pipeline.run(
            math_model=session.state.math_model,
            solver_id="dwave_hybrid_cqm",
            project_id="abc123",
        )
    """

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
            # Apply policy-driven variable bounds + big_m (on deepcopy to avoid mutation)
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

            compiler = get_compiler(solver_id)
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
                },
                "model_stats": {
                    "total_variables": compile_result.variable_count,
                    "total_constraints": compile_result.constraint_count,
                },
                "math_model": math_model,
                "warnings": compile_result.warnings or [],
            }
            stage5_result = registry.run_stage(5, stage5_ctx)
            if stage5_result.items:
                # Store for inclusion in final summary
                self._stage5_validation = stage5_result.to_dict()
                logger.info(
                    f"Stage5 validation: errors={stage5_result.error_count}, "
                    f"warnings={stage5_result.warning_count}"
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
                return PipelineResult(
                    success=False, phase="execute", solver_id=solver_id,
                    solver_name=solver_name,
                    compile_result=compile_result,
                    compile_time_sec=round(compile_time, 3),
                    execute_result=execute_result,
                    error=f"Execution failed: {execute_result.error or execute_result.status}"
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

