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
            return self._compile_cpsat(sp_problem, math_model=math_model,
                                        params=bound_data.get("parameters", {}))
        except Exception as e:
            logger.error(f"SP compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _compile_cpsat(self, problem: SetPartitioningProblem, **kwargs) -> CompileResult:
        """SetPartitioningProblem → CP-SAT 모델 변환"""
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()

        # ── 1. 변수: dense index 기반 (#1) ──
        # col.id는 hole이 있을 수 있으므로 0~N-1 dense index 사용
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
                # #1: uncovered task → 즉시 실패 (continue 금지)
                return CompileResult(
                    success=False,
                    error=f"Task {tid} has no covering column. SP infeasible.",
                )
            model.add(sum(z[cid] for cid in col_ids) == 1)
            coverage_count += 1

        # ── 3. 추가 제약 (SP problem에서 정의) ──
        extra_count = 0
        for constraint in problem.extra_constraints:
            col_vars = [z[cid] for cid in constraint.column_ids if cid in z]
            # #2: 부분 적용 감지
            missing = [cid for cid in constraint.column_ids if cid not in z]
            if missing:
                logger.warning(f"SP: constraint '{constraint.name}' has {len(missing)} missing columns")
            if not col_vars:
                logger.error(f"SP: constraint '{constraint.name}' has no valid columns!")
                continue
            if constraint.operator == "==":
                model.add(sum(col_vars) == constraint.rhs)
            elif constraint.operator == "<=":
                model.add(sum(col_vars) <= constraint.rhs)
            elif constraint.operator == ">=":
                model.add(sum(col_vars) >= constraint.rhs)
            extra_count += 1
            logger.info(f"SP: {constraint.label}")

        # ── 4. 목적함수: ObjectiveBuilder (solver-independent) ──
        from engine.compiler.objective_builder import ObjectiveBuilder, ObjectiveConfig, extract_objective_type

        math_model = kwargs.get("math_model", {})
        objective_type = extract_objective_type(math_model)
        obj_config = ObjectiveConfig.from_params(kwargs.get("params", {}))

        builder = ObjectiveBuilder(problem.columns, obj_config)
        scores = builder.build(objective_type, kwargs.get("params", {}))

        # fallback: missing score → 충분히 큰 penalty (#3: 과도하지 않게)
        max_score = max(scores.values(), default=1000)
        penalty = max_score * 10
        missing_count = 0

        cost_terms = []
        for col in problem.columns:
            score = scores.get(col.id)
            if score is None:
                score = penalty
                missing_count += 1
            cost_terms.append(score * z[col.id])
        model.minimize(sum(cost_terms))

        if missing_count > 0:
            logger.warning(f"SP: {missing_count} columns missing scores (fallback penalty applied)")

        total_constraints = coverage_count + extra_count

        logger.info(
            f"SP compiled: {len(z)} vars, {coverage_count} coverage, "
            f"{extra_count} extra, {problem.num_tasks} tasks"
        )

        # ── SP 진단 정보 (INFEASIBLE 시 사용자 피드백용) ──
        sp_diagnostics = _build_sp_diagnostics(problem)

        return CompileResult(
            success=True,
            solver_model=model,
            solver_type="ortools_cp",
            variable_count=len(z),
            constraint_count=total_constraints,
            variable_map={"z": z, "col_index": col_index},
            metadata={
                "model_type": "SetPartitioning",
                "engine": "ortools_cp_sat",
                "column_count": problem.num_columns,
                "task_count": problem.num_tasks,
                "coverage_constraints": coverage_count,
                "duty_map": {c.id: c for c in problem.columns},
                "sp_diagnostics": sp_diagnostics,
            },
        )


def _build_sp_diagnostics(problem: SetPartitioningProblem) -> Dict[str, Any]:
    """
    SP 문제 사전 진단 정보 구축.

    INFEASIBLE 발생 시 사용자에게 구체적 원인 제공:
    - coverage 밀도 (어떤 task가 취약한지)
    - crew count 실현 가능성 (column_type 분포)
    - 잠재적 충돌 (제약 간 모순)
    """
    from collections import Counter

    # column_type별 수
    type_dist = Counter(c.column_type for c in problem.columns)

    # task별 coverage density
    density = {tid: len(cids) for tid, cids in problem.task_to_columns.items()}
    min_density = min(density.values()) if density else 0
    weak_tasks = [tid for tid, d in density.items() if d <= 3]

    # extra constraint 실현 가능성 체크
    constraint_risks = []
    for con in problem.extra_constraints:
        available = len(con.column_ids)
        if con.operator == "==" and available < con.rhs:
            constraint_risks.append({
                "constraint": con.name,
                "label": con.label,
                "required": con.rhs,
                "available_columns": available,
                "risk": "INFEASIBLE_CERTAIN",
                "message": f"{con.label}: 필요한 {con.rhs}개보다 후보가 {available}개 부족",
            })
        elif con.operator == "==" and available < con.rhs * 2:
            constraint_risks.append({
                "constraint": con.name,
                "label": con.label,
                "required": con.rhs,
                "available_columns": available,
                "risk": "INFEASIBLE_LIKELY",
                "message": f"{con.label}: 후보 {available}개로 {con.rhs}개 선택은 매우 빡빡",
            })

    # 복합 제약 충돌 체크 (total = day + night)
    total_con = next((c for c in problem.extra_constraints if c.name == "total_columns"), None)
    day_con = next((c for c in problem.extra_constraints if c.name == "day_columns"), None)
    night_con = next((c for c in problem.extra_constraints if c.name == "night_columns"), None)
    if total_con and day_con and night_con:
        if day_con.rhs + night_con.rhs != total_con.rhs:
            constraint_risks.append({
                "constraint": "crew_count_sum",
                "risk": "INFEASIBLE_CERTAIN",
                "message": f"day({day_con.rhs}) + night({night_con.rhs}) = "
                           f"{day_con.rhs + night_con.rhs} ≠ total({total_con.rhs})",
            })

    diagnostics = {
        "column_type_distribution": dict(type_dist),
        "task_count": problem.num_tasks,
        "column_count": problem.num_columns,
        "min_coverage_density": min_density,
        "weak_tasks_count": len(weak_tasks),
        "weak_tasks_sample": weak_tasks[:10],
        "degree_1_count": len(problem.degree_1_tasks),
        "constraint_risks": constraint_risks,
    }

    # 리스크 경고 로그
    for risk in constraint_risks:
        if risk["risk"] == "INFEASIBLE_CERTAIN":
            logger.error(f"SP diagnostic: {risk['message']}")
        elif risk["risk"] == "INFEASIBLE_LIKELY":
            logger.warning(f"SP diagnostic: {risk['message']}")

    return diagnostics
