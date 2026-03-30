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

from engine.compiler.base import BaseSPCompiler, CompileResult
from engine.column_generator import FeasibleColumn
from engine.compiler.sp_problem import SetPartitioningProblem, build_sp_problem

logger = logging.getLogger(__name__)


class SetPartitioningCompiler(BaseSPCompiler):
    """CP-SAT 기반 Set Partitioning 컴파일러"""

    def _compile_backend(self, sp_problem, math_model: Dict,
                          bound_data: Dict, **kwargs) -> CompileResult:
        """BaseSPCompiler → CP-SAT 변환"""
        return self._compile_cpsat(
            sp_problem,
            math_model=math_model,
            params=bound_data.get("parameters", {}),
        )

    def _compile_cpsat(self, problem: SetPartitioningProblem,
                       math_model: Dict, params: Dict) -> CompileResult:
        """SetPartitioningProblem → CP-SAT 모델 변환"""
        from ortools.sat.python import cp_model

        # ── INFEASIBLE 사전 차단: 확정적 원인이 있으면 solver 탐색 불필요 ──
        sp_diagnostics = problem.diagnostics
        certain_risks = [
            r for r in sp_diagnostics.get("constraint_risks", [])
            if r["risk"] == "INFEASIBLE_CERTAIN"
        ]
        if certain_risks:
            messages = [r["message"] for r in certain_risks]
            return CompileResult(
                success=False,
                error=f"SP known infeasible: {'; '.join(messages)}",
                metadata={"sp_diagnostics": sp_diagnostics},
            )

        model = cp_model.CpModel()

        # ── 1. 변수: dense index 기반 ──
        col_index = {col.id: i for i, col in enumerate(problem.columns)}
        z = {}
        for col in problem.columns:
            idx = col_index[col.id]
            z[col.id] = model.new_bool_var(f"z_{idx}")

        # ── 2. Coverage 제약: 각 task를 정확히 1개 column에 배정 ──
        coverage_count = 0
        for tid in problem.task_ids:
            col_ids = problem.task_to_columns.get(tid, [])
            if not col_ids:
                return CompileResult(
                    success=False,
                    error=f"Task {tid} has no covering column. SP infeasible.",
                )
            model.add(sum(z[cid] for cid in col_ids) == 1)
            coverage_count += 1

        # ── 3. 추가 제약 (hard) + soft slack 변수 수집 ──
        extra_count = 0
        soft_count = 0
        slack_penalty_terms = []  # soft constraint의 penalty term (objective에 추가)

        for constraint in problem.extra_constraints:
            # 3-1. 표현식 구축 (coefficient 유무에 따라)
            if constraint.coefficients:
                expr_terms = []
                for cid, coeff in constraint.coefficients.items():
                    if cid in z:
                        expr_terms.append(int(coeff * 1000) * z[cid])
                if not expr_terms:
                    if not constraint.is_soft:
                        return CompileResult(
                            success=False,
                            error=f"Constraint '{constraint.name}' has no applicable columns — infeasible",
                        )
                    continue
                rhs_scaled = int(constraint.rhs * 1000)
                use_scaled = True
            else:
                col_vars = [z[cid] for cid in constraint.column_ids if cid in z]
                if not col_vars:
                    if not constraint.is_soft:
                        return CompileResult(
                            success=False,
                            error=f"Constraint '{constraint.name}' ({constraint.label}) has no applicable columns — infeasible",
                        )
                    continue
                expr_terms = col_vars
                rhs_scaled = int(constraint.rhs)
                use_scaled = False

            expr = sum(expr_terms)

            # 3-2. soft: slack 변수 + 완화된 제약 + penalty term
            if constraint.is_soft:
                # slack bound: coefficient 사용 시 스케일링 고려
                if use_scaled:
                    slack_ub = max(abs(rhs_scaled), 1000000)  # 충분히 큰 상한
                else:
                    slack_ub = max(int(constraint.rhs) * 2, 1000)

                slack = model.new_int_var(0, slack_ub, f"slack_{constraint.name}")

                if constraint.operator == "<=":
                    model.add(expr <= rhs_scaled + slack)
                elif constraint.operator == ">=":
                    model.add(expr >= rhs_scaled - slack)
                elif constraint.operator == "==":
                    model.add(expr <= rhs_scaled + slack)
                    model.add(expr >= rhs_scaled - slack)

                slack_penalty_terms.append((slack, constraint.penalty_weight, constraint.name))
                soft_count += 1
                logger.info(f"SP (soft): {constraint.label} [weight={constraint.penalty_weight}]")
            else:
                # 3-3. hard: 기존 동작
                if constraint.operator == "==":
                    model.add(expr == rhs_scaled)
                elif constraint.operator == "<=":
                    model.add(expr <= rhs_scaled)
                elif constraint.operator == ">=":
                    model.add(expr >= rhs_scaled)
                logger.info(f"SP (hard): {constraint.label}")

            extra_count += 1

        # ── 4. 목적함수: column score + soft penalty ──
        from engine.compiler.objective_builder import ObjectiveBuilder, ObjectiveConfig, extract_objective_type

        objective_type = extract_objective_type(math_model)
        obj_config = ObjectiveConfig.from_params(
            params, domain=math_model.get("domain"))

        builder = ObjectiveBuilder(problem.columns, obj_config)
        scores = builder.build(objective_type, params)

        if not scores:
            return CompileResult(
                success=False,
                error="ObjectiveBuilder returned no scores — check objective configuration",
            )

        # fallback: missing score → 충분히 큰 penalty
        max_score = max(scores.values())
        penalty = max_score * 10
        missing_count = 0

        cost_terms = []
        for col in problem.columns:
            score = scores.get(col.id)
            if score is None:
                score = penalty
                missing_count += 1
            cost_terms.append(score * z[col.id])

        # soft constraint penalty: weight * slack (score 스케일에 맞춤)
        # penalty_weight=1.0은 "max_score와 동등한 중요도"
        for slack_var, weight, name in slack_penalty_terms:
            # 스케일링: penalty_weight * max_score로 score 스케일에 비례
            scaled_weight = int(weight * max_score)
            cost_terms.append(scaled_weight * slack_var)
            logger.info(
                f"SP objective: +{scaled_weight} * slack_{name} "
                f"(weight={weight}, max_score={max_score})"
            )

        model.minimize(sum(cost_terms))

        if missing_count > 0:
            logger.warning(f"SP: {missing_count} columns missing scores (fallback penalty applied)")
        if soft_count > 0:
            logger.info(f"SP: {soft_count} soft constraints with slack variables")

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
                "col_index": col_index,
                "column_count": problem.num_columns,
                "task_count": problem.num_tasks,
                "coverage_constraints": coverage_count,
                "duty_map": {c.id: c for c in problem.columns},
                "all_task_ids": problem.task_ids,
                "sp_diagnostics": sp_diagnostics,
            },
        )
