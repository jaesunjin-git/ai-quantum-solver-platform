# engine/solver_pipeline.py
# ============================================================
# Solver Pipeline: 수학 모델 IR -> 컴파일 -> 실행 -> 결과 통합
# ============================================================

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.compiler import get_compiler
from engine.compiler.base import DataBinder, CompileResult
from engine.executor import get_executor
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
        time_limit_sec: int = 300,
        **kwargs,
    ) -> PipelineResult:
        """전체 파이프라인 실행"""

        logger.info(f"Pipeline: solver={solver_id}, project={project_id}")

        #  Phase 1: Data Binding 
        try:
            binder = DataBinder(project_id)
            bound_data = binder.bind_all(math_model)
            logger.info(
                f"DataBinder: sets={list(bound_data['set_sizes'].items())}, "
                f"params={len(bound_data['parameters'])}"
            )
        except Exception as e:
            logger.error(f"DataBinding failed: {e}", exc_info=True)
            return PipelineResult(
                success=False, phase="bind", solver_id=solver_id,
                error=f"Data binding failed: {str(e)}"
            )

        #  Phase 2: Compile 
        try:
            compiler = get_compiler(solver_id)
            logger.info(f"Compiler: {type(compiler).__name__}")

            compile_start = time.time()
            compile_result = compiler.compile(math_model, bound_data, **kwargs)
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
            compile_result, compile_time, execute_result
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
    ) -> Dict[str, Any]:
        """프론트엔드에 보낼 결과 요약 생성"""

        # 비영 솔루션 변수 개수
        nonzero = 0
        for vid, val in execute_result.solution.items():
            if isinstance(val, dict):
                nonzero += len(val)
            elif val != 0:
                nonzero += 1

        summary = {
            "solver_id": solver_id,
            "solver_name": solver_name,
            "solver_type": compile_result.solver_type,
            "status": execute_result.status,
            "objective_value": execute_result.objective_value,

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

            "compile_warnings": compile_result.warnings,
        }

        return summary
