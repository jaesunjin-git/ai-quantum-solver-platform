"""
set_partitioning_compiler.py ────────────────────────────────
Set Partitioning Compiler (CP-SAT backend).

Backend-agnostic SP problem을 CP-SAT 모델로 변환.
다른 backend(CQM, BQM 등)는 별도 compiler 파일에서 구현.

모델:
  변수: z[k] ∈ {0,1} — column k를 선택
  제약: ∀i ∈ tasks, sum(z[k] for k if i ∈ column[k].tasks) == 1
  목적: min sum(z[k] * cost[k])
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from engine.compiler.base import BaseCompiler, CompileResult
from engine.column_generator import FeasibleColumn
from engine.compiler.sp_problem import SetPartitioningProblem, build_sp_problem

logger = logging.getLogger(__name__)


class SetPartitioningCompiler(BaseCompiler):
    """CP-SAT 기반 Set Partitioning 컴파일러"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """
        Set Partitioning 모델 컴파일.

        kwargs:
          duties: List[FeasibleColumn] — ColumnGenerator 출력
          sp_problem: SetPartitioningProblem — 직접 제공 시 (duties 대신)
        """
        # SP problem 구축 (또는 직접 제공)
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

        # 유효성 검증
        valid, errors = sp_problem.validate()
        if not valid:
            return CompileResult(
                success=False,
                error=f"SP problem invalid: {'; '.join(errors)}",
            )

        try:
            return self._compile_cpsat(sp_problem)
        except Exception as e:
            logger.error(f"SP compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _compile_cpsat(self, problem: SetPartitioningProblem) -> CompileResult:
        """SetPartitioningProblem → CP-SAT 모델 변환"""
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()

        # ── 1. 변수: z[k] (column 선택) ──
        z = {}
        for col in problem.columns:
            z[col.id] = model.new_bool_var(f"z_{col.id}")

        # ── 2. Coverage 제약: 각 task를 정확히 1개 column에 배정 ──
        coverage_count = 0
        for tid in problem.task_ids:
            col_ids = problem.task_to_columns.get(tid, [])
            if not col_ids:
                continue  # validate()에서 이미 체크
            model.add(sum(z[cid] for cid in col_ids) == 1)
            coverage_count += 1

        # ── 3. 추가 제약 (SP problem에서 정의) ──
        extra_count = 0
        for constraint in problem.extra_constraints:
            col_vars = [z[cid] for cid in constraint.column_ids if cid in z]
            if not col_vars:
                continue
            if constraint.operator == "==":
                model.add(sum(col_vars) == constraint.rhs)
            elif constraint.operator == "<=":
                model.add(sum(col_vars) <= constraint.rhs)
            elif constraint.operator == ">=":
                model.add(sum(col_vars) >= constraint.rhs)
            extra_count += 1
            logger.info(f"SP: {constraint.label}")

        # ── 4. 목적함수: lexicographic (column 수 우선 + secondary cost) ──
        # Big-M: 1 column 줄이는 것이 어떤 secondary 차이보다 항상 우선
        def _secondary_cost(col):
            tc = len(col.trips)
            short_penalty = 30 * max(0, 8 - tc) ** 2  # 짧은 column 억제
            idle_penalty = col.idle_minutes
            return short_penalty + idle_penalty

        max_secondary = max((_secondary_cost(c) for c in problem.columns), default=1)
        big_m = max_secondary + 1  # column 1개 > 최대 secondary

        cost_terms = []
        for col in problem.columns:
            total_cost = big_m + _secondary_cost(col)
            cost_terms.append(total_cost * z[col.id])
        model.minimize(sum(cost_terms))

        total_constraints = coverage_count + extra_count

        logger.info(
            f"SP compiled: {len(z)} vars, {coverage_count} coverage, "
            f"{extra_count} extra, {problem.num_tasks} tasks"
        )

        return CompileResult(
            success=True,
            solver_model=model,
            solver_type="ortools_cp",
            variable_count=len(z),
            constraint_count=total_constraints,
            variable_map={"z": z},
            metadata={
                "model_type": "SetPartitioning",
                "engine": "ortools_cp_sat",
                "column_count": problem.num_columns,
                "task_count": problem.num_tasks,
                "coverage_constraints": coverage_count,
                "duty_map": {c.id: c for c in problem.columns},
            },
        )
