"""
cqm_compiler.py ────────────────────────────────────────────
D-Wave CQM backend Set Partitioning 컴파일러.

SetPartitioningProblem → D-Wave ConstrainedQuadraticModel 변환.
ObjectiveBuilder를 통해 solver-independent objective 사용.

GR-1: engine 내부 모듈. domain import 없음.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from engine.compiler.base import BaseCompiler, CompileResult
from engine.compiler.sp_problem import SetPartitioningProblem, build_sp_problem
from engine.column_generator import FeasibleColumn

logger = logging.getLogger(__name__)


class CQMCompiler(BaseCompiler):
    """D-Wave CQM 기반 Set Partitioning 컴파일러"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """SetPartitioningProblem → D-Wave CQM 변환."""
        sp_problem = kwargs.pop("sp_problem", None)
        if sp_problem is None:
            columns: List[FeasibleColumn] = kwargs.pop("duties", [])
            if not columns:
                return CompileResult(
                    success=False,
                    error="No columns provided. Run ColumnGenerator first.",
                )
            params = bound_data.get("parameters", {})
            sp_problem = build_sp_problem(columns, params)

        valid, errors = sp_problem.validate()
        if not valid:
            return CompileResult(
                success=False,
                error=f"SP problem invalid: {'; '.join(errors)}",
            )

        try:
            return self._compile_cqm(sp_problem, math_model=math_model, **kwargs)
        except ImportError as e:
            logger.error(f"D-Wave SDK not installed: {e}")
            return CompileResult(
                success=False,
                error=f"D-Wave SDK not available: {e}. "
                      f"Install: pip install dwave-ocean-sdk",
            )
        except Exception as e:
            logger.error(f"CQM compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    # CQM용 column cap (100K는 compile에 263초 소요)
    CQM_MAX_COLUMNS = 20000

    def _compile_cqm(self, problem: SetPartitioningProblem, **kwargs) -> CompileResult:
        """SetPartitioningProblem → CQM 모델 변환 (dimod.quicksum 최적화)"""
        from dimod import Binary, ConstrainedQuadraticModel, quicksum

        t0 = time.time()

        # ── 0. Column cap: CQM은 대규모 변수에 느리므로 제한 ──
        columns = problem.columns
        if len(columns) > self.CQM_MAX_COLUMNS:
            # cost 기준 상위 N개만 사용 (다양성은 SP problem에서 이미 보장)
            columns = sorted(columns, key=lambda c: c.cost)[:self.CQM_MAX_COLUMNS]
            logger.info(f"CQM: column cap applied {len(problem.columns)} → {len(columns)}")

            # task_to_columns 재구축 (cap 적용된 columns만)
            col_id_set = {c.id for c in columns}
            task_to_columns = {}
            for c in columns:
                for tid in c.trips:
                    task_to_columns.setdefault(tid, []).append(c.id)
        else:
            task_to_columns = problem.task_to_columns

        cqm = ConstrainedQuadraticModel()

        # ── 1. 변수: z[k] (binary) — 한번에 생성 ──
        z = {col.id: Binary(f"z_{col.id}") for col in columns}

        # ── 2. Coverage 제약: quicksum 사용 (dimod 최적화) ──
        coverage_count = 0
        for tid in problem.task_ids:
            col_ids = task_to_columns.get(tid, [])
            if not col_ids:
                continue
            cqm.add_constraint(
                quicksum(z[cid] for cid in col_ids) == 1,
                label=f"cover_{tid}",
            )
            coverage_count += 1

        # ── 3. 추가 제약: quicksum 사용 ──
        extra_count = 0
        for constraint in problem.extra_constraints:
            col_vars = [z[cid] for cid in constraint.column_ids if cid in z]
            if not col_vars:
                continue
            expr = quicksum(col_vars)
            if constraint.operator == "==":
                cqm.add_constraint(expr == constraint.rhs, label=constraint.name)
            elif constraint.operator == "<=":
                cqm.add_constraint(expr <= constraint.rhs, label=constraint.name)
            elif constraint.operator == ">=":
                cqm.add_constraint(expr >= constraint.rhs, label=constraint.name)
            extra_count += 1
            logger.info(f"CQM: {constraint.label}")

        # ── 4. 목적함수: ObjectiveBuilder + quicksum ──
        from engine.compiler.objective_builder import ObjectiveBuilder, ObjectiveConfig, extract_objective_type

        math_model = kwargs.get("math_model", {})
        objective_type = extract_objective_type(math_model)
        obj_config = ObjectiveConfig.from_params(kwargs.get("params", {}))

        builder = ObjectiveBuilder(columns, obj_config)
        scores = builder.build(objective_type, kwargs.get("params", {}))

        # quicksum으로 objective 구축
        obj_terms = [
            (scores.get(col.id, 1000) / 1000.0) * z[col.id]
            for col in columns
        ]
        cqm.set_objective(quicksum(obj_terms))

        compile_time = time.time() - t0
        total_constraints = coverage_count + extra_count

        # ── SP 진단 정보 ──
        from engine.compiler.set_partitioning_compiler import _build_sp_diagnostics
        sp_diagnostics = _build_sp_diagnostics(problem)

        logger.info(
            f"CQM compiled: {len(z)} vars, {coverage_count} coverage, "
            f"{extra_count} extra, {problem.num_tasks} tasks, "
            f"objective={objective_type}, compile_time={compile_time:.2f}s"
        )

        return CompileResult(
            success=True,
            solver_model=cqm,
            solver_type="dwave_cqm",
            variable_count=len(z),
            constraint_count=total_constraints,
            variable_map={"z": z},
            metadata={
                "model_type": "SetPartitioning",
                "engine": "dwave_cqm",
                "column_count": len(columns),
                "task_count": problem.num_tasks,
                "coverage_constraints": coverage_count,
                "duty_map": {c.id: c for c in columns},
                "compile_time": compile_time,
                "sp_diagnostics": sp_diagnostics,
            },
        )


class CQMExecutor:
    """
    D-Wave LeapHybridCQMSampler 실행기.

    solver capacity/time_limit은 런타임에서 조회 (하드코딩 금지).
    """

    def execute(
        self,
        compile_result: CompileResult,
        time_limit_sec: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """CQM 모델을 D-Wave에서 실행."""
        from dwave.system import LeapHybridCQMSampler
        from engine.executor.base import ExecuteResult

        cqm = compile_result.solver_model

        # ── Sampler 초기화 + capacity 런타임 조회 ──
        sampler = LeapHybridCQMSampler()
        props = sampler.properties

        max_vars = props.get("maximum_number_of_variables")
        max_constraints = props.get("maximum_number_of_constraints")
        num_vars = compile_result.variable_count
        num_constraints = compile_result.constraint_count

        if max_vars is not None and num_vars > max_vars:
            return ExecuteResult(
                success=False, solver_type="dwave_cqm", status="ERROR",
                error=f"CQM variable limit exceeded: {num_vars} > {max_vars}",
            )
        if max_constraints is not None and num_constraints > max_constraints:
            return ExecuteResult(
                success=False, solver_type="dwave_cqm", status="ERROR",
                error=f"CQM constraint limit exceeded: {num_constraints} > {max_constraints}",
            )

        logger.info(f"D-Wave CQM: {num_vars} vars, {num_constraints} constraints, "
                     f"capacity: max_vars={max_vars}, max_constraints={max_constraints}")

        # ── time_limit: min_time_limit() 런타임 조회 ──
        min_time = sampler.min_time_limit(cqm)
        effective_limit = max(time_limit_sec or 0, min_time)
        if time_limit_sec and time_limit_sec < min_time:
            logger.warning(f"CQM: time_limit {time_limit_sec}s < min_time_limit {min_time}s, "
                          f"using {min_time}s")

        logger.info(f"D-Wave CQM: submitting (time_limit={effective_limit}s)")

        # ── 실행 ──
        t0 = time.time()
        sampleset = sampler.sample_cqm(cqm, time_limit=effective_limit)
        wall_time = time.time() - t0

        # ── 결과 해석 ──
        feasible = sampleset.filter(lambda s: s.is_feasible)
        if len(feasible) == 0:
            logger.warning("CQM: no feasible solutions found")
            return ExecuteResult(
                success=False, solver_type="dwave_cqm", status="INFEASIBLE",
                execution_time_sec=round(wall_time, 3),
                solver_info={
                    "sample_count": len(sampleset),
                    "feasible_count": 0,
                    "wall_time": wall_time,
                },
            )

        # 최적 feasible sample
        best = feasible.first
        sample = best.sample

        # z 변수 추출 → solution dict
        solution = {"z": {}}
        for var_name, val in sample.items():
            if var_name.startswith("z_"):
                col_id = var_name[2:]
                solution["z"][col_id] = int(val)

        objective_value = best.energy
        selected_count = sum(1 for v in solution["z"].values() if v > 0)

        # ── Post-validation: coverage ==1 검증 ──
        violations = self._validate_coverage(solution, compile_result)

        status = "FEASIBLE"
        if violations:
            status = "INFEASIBLE_POST"
            logger.warning(f"CQM post-validation: {len(violations)} coverage violations")

        logger.info(f"D-Wave CQM: {status}, obj={objective_value:.2f}, "
                     f"selected={selected_count}, wall_time={wall_time:.1f}s, "
                     f"feasible={len(feasible)}/{len(sampleset)}")

        return ExecuteResult(
            success=status != "INFEASIBLE_POST",
            solver_type="dwave_cqm",
            status=status,
            objective_value=objective_value,
            solution=solution,
            execution_time_sec=round(wall_time, 3),
            solver_info={
                "sample_count": len(sampleset),
                "feasible_count": len(feasible),
                "selected_columns": selected_count,
                "wall_time": wall_time,
                "timing": sampleset.info.get("timing", {}),
                "coverage_violations": violations if violations else None,
            },
        )

    @staticmethod
    def _validate_coverage(
        solution: Dict, compile_result: CompileResult
    ) -> Dict[int, int]:
        """
        CQM 결과의 coverage ==1 검증.

        CQM은 equality constraint가 약할 수 있으므로
        post-solve에서 반드시 검증.

        Returns:
            {trip_id: actual_count} — 위반 trip만 (count != 1)
        """
        duty_map = compile_result.metadata.get("duty_map", {})
        z_solution = solution.get("z", {})

        from collections import defaultdict
        coverage = defaultdict(int)

        for col_id_str, val in z_solution.items():
            if int(val) == 1:
                col = duty_map.get(int(col_id_str))
                if col:
                    for tid in col.trips:
                        coverage[tid] += 1

        violations = {
            tid: cnt for tid, cnt in coverage.items()
            if cnt != 1
        }
        return violations
