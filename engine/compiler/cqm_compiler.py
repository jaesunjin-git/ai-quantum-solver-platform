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

from engine.compiler.base import BaseSPCompiler, CompileResult
from engine.compiler.sp_problem import SetPartitioningProblem, build_sp_problem
from engine.column_generator import FeasibleColumn

logger = logging.getLogger(__name__)


class CQMCompiler(BaseSPCompiler):
    """D-Wave CQM 기반 Set Partitioning 컴파일러"""

    def _compile_backend(self, sp_problem, math_model: Dict,
                          bound_data: Dict, **kwargs) -> CompileResult:
        """BaseSPCompiler → D-Wave CQM 변환"""
        return self._compile_cqm(sp_problem, math_model=math_model, **kwargs)

    @staticmethod
    def _load_cqm_config() -> dict:
        """configs/hybrid_strategy.yaml에서 CQM compiler 설정 로딩."""
        import os, yaml
        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "configs", "hybrid_strategy.yaml"
        )
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("cqm_compiler", {})
        except Exception:
            return {}

    @staticmethod
    def _cap_with_coverage(
        columns: List[FeasibleColumn],
        task_to_columns: Dict,
        task_ids: List[int],
        max_columns: int,
    ) -> List[FeasibleColumn]:
        """
        Column cap 적용 시 coverage 보장 (greedy set cover anchor).

        1단계: Greedy set cover로 최소 anchor 확보 (gain/cost 비율)
        2단계: 나머지 budget을 cost 기준으로 채움

        fallback: greedy anchor가 30% 이상이면 기존 단순 anchor로 전환
        """
        col_map = {c.id: c for c in columns}
        all_tasks = set(task_ids)

        # ── 1단계: Greedy set cover anchor ──
        # gain(새로 커버하는 task 수) / cost 비율이 높은 column 우선
        anchor_ids = set()
        covered = set()

        # column별 task set 사전 구축
        col_tasks = {c.id: set(c.trips) for c in columns}

        # task별 column 인덱스 (빠른 탐색)
        task_to_col_set = {}
        for c in columns:
            for tid in c.trips:
                task_to_col_set.setdefault(tid, []).append(c)

        while covered < all_tasks:
            # uncovered task 중 가장 적은 column을 가진 task부터 (MRV)
            best_id = None
            best_score = -1.0

            uncovered_tasks = all_tasks - covered
            # 샘플링: uncovered task의 column만 탐색 (전체 스캔 방지)
            candidate_ids = set()
            for tid in uncovered_tasks:
                for c in task_to_col_set.get(tid, []):
                    if c.id not in anchor_ids:
                        candidate_ids.add(c.id)

            for cid in candidate_ids:
                c = col_map[cid]
                gain = len(col_tasks[c.id] & uncovered_tasks)
                if gain == 0:
                    continue
                score = gain / max(c.cost, 0.01)
                if score > best_score:
                    best_score = score
                    best_id = c.id

            if best_id is None:
                break

            anchor_ids.add(best_id)
            covered |= col_tasks[best_id]

        # ── fallback: anchor가 budget 30% 초과 시 단순 방식으로 전환 ──
        anchor_limit = int(max_columns * 0.3)
        if len(anchor_ids) > anchor_limit:
            logger.warning(f"CQM cap: greedy anchor {len(anchor_ids)} > 30% limit "
                          f"({anchor_limit}), falling back to simple anchor")
            anchor_ids = set()
            covered = set()
            for tid in task_ids:
                if tid in covered:
                    continue
                col_ids = task_to_columns.get(tid, [])
                if not col_ids:
                    continue
                best_cid = min(col_ids,
                               key=lambda cid: col_map[cid].cost if cid in col_map else float('inf'))
                anchor_ids.add(best_cid)
                covered |= col_tasks.get(best_cid, set())

        logger.info(f"CQM cap: {len(anchor_ids)} anchor columns "
                     f"(covers {len(covered)}/{len(all_tasks)} tasks)")

        # ── 2단계: 나머지 budget을 cost 기준으로 채움 ──
        remaining_budget = max_columns - len(anchor_ids)
        if remaining_budget > 0:
            candidates = sorted(
                [c for c in columns if c.id not in anchor_ids],
                key=lambda c: c.cost
            )
            fill_ids = {c.id for c in candidates[:remaining_budget]}
        else:
            fill_ids = set()

        selected_ids = anchor_ids | fill_ids
        result = [c for c in columns if c.id in selected_ids]

        # ── coverage 최종 검증 ──
        final_covered = set()
        for c in result:
            final_covered.update(c.trips)
        uncovered = all_tasks - final_covered
        if uncovered:
            logger.warning(f"CQM cap: {len(uncovered)} tasks STILL uncovered!")
            # auto-repair: uncovered task의 column 강제 추가
            for tid in uncovered:
                col_ids = task_to_columns.get(tid, [])
                if col_ids:
                    repair_cid = min(col_ids,
                                     key=lambda cid: col_map[cid].cost if cid in col_map else float('inf'))
                    result.append(col_map[repair_cid])
                    logger.info(f"CQM cap: auto-repair added column {repair_cid} for task {tid}")

        return result

    def _compile_cqm(self, problem: SetPartitioningProblem, **kwargs) -> CompileResult:
        """SetPartitioningProblem → CQM 모델 변환 (dimod.quicksum 최적화)"""
        from dimod import Binary, ConstrainedQuadraticModel, quicksum

        t0 = time.time()

        # ── 0. Column cap + constraint 전략: YAML config 기반 ──
        is_hybrid = kwargs.get("is_hybrid", False)
        cqm_cfg = self._load_cqm_config()
        mode_cfg = cqm_cfg.get("hybrid" if is_hybrid else "standalone", {})
        cap = mode_cfg.get("max_columns", 20000)
        constraint_mode = mode_cfg.get("constraint_mode", "hard" if not is_hybrid else "soft")
        weight_multiplier = mode_cfg.get("soft_weight_multiplier", 10)
        columns = problem.columns
        if len(columns) > cap:
            columns = self._cap_with_coverage(
                problem.columns, problem.task_to_columns, problem.task_ids, cap
            )
            logger.info(f"CQM: column cap {len(problem.columns)} → {len(columns)} "
                        f"({'hybrid' if is_hybrid else 'standalone'})")

            # task_to_columns 재구축
            task_to_columns = {}
            for c in columns:
                for tid in c.trips:
                    task_to_columns.setdefault(tid, []).append(c.id)
        else:
            task_to_columns = problem.task_to_columns

        cqm = ConstrainedQuadraticModel()

        # ── 1. 변수: z[k] (binary) — 한번에 생성 ──
        z = {col.id: Binary(f"z_{col.id}") for col in columns}

        # ── 2. 목적함수 먼저 구축 (objective_scale → weight 결정에 사용) ──
        from engine.compiler.objective_builder import ObjectiveBuilder, ObjectiveConfig, extract_objective_type

        math_model = kwargs.get("math_model", {})
        objective_type = extract_objective_type(math_model)
        obj_config = ObjectiveConfig.from_params(
            kwargs.get("params", {}),
            domain=math_model.get("domain"),
        )

        builder = ObjectiveBuilder(columns, obj_config)
        scores = builder.build(objective_type, kwargs.get("params", {}))

        max_score = max(scores.values(), default=1000)

        # quicksum으로 objective 구축 (정규화 스케일링)
        obj_terms = [
            (scores.get(col.id, max_score) / max(max_score, 1)) * z[col.id]
            for col in columns
        ]
        cqm.set_objective(quicksum(obj_terms))

        # objective_scale: weight 자동 결정의 기준
        num_columns = len(columns)
        objective_scale = max(num_columns, max_score)

        # ── 3. Coverage 제약: YAML config 기반 전략 ──
        # "hard" → D-Wave 네이티브 hard (weight=None, feasible = exact partition)
        # "soft" → objective_scale × multiplier (hint 품질 우선)
        if constraint_mode == "hard":
            structural_weight = None
        else:
            structural_weight = objective_scale * weight_multiplier

        coverage_count = 0
        for tid in problem.task_ids:
            col_ids = task_to_columns.get(tid, [])
            if not col_ids:
                logger.error(f"CQM: task {tid} has no covering column!")
                continue
            constraint_kwargs = {"label": f"cover_{tid}"}
            if structural_weight is not None:
                constraint_kwargs["weight"] = structural_weight
            cqm.add_constraint(
                quicksum(z[cid] for cid in col_ids) == 1,
                **constraint_kwargs,
            )
            coverage_count += 1

        # ── 4. 추가 제약: 단독=hard, Hybrid=soft ──
        extra_count = 0
        for constraint in problem.extra_constraints:
            con_kwargs = {"label": constraint.name}

            # soft constraint: D-Wave native weight 사용
            if constraint.is_soft:
                con_kwargs["weight"] = constraint.penalty_weight
            elif structural_weight is not None:
                con_kwargs["weight"] = structural_weight
            # else: hard (no weight → CQM treats as hard)

            # coefficient 지원: Σ coeff[k] * z[k] op rhs
            if constraint.coefficients:
                terms = [
                    coeff * z[cid]
                    for cid, coeff in constraint.coefficients.items()
                    if cid in z
                ]
                if not terms:
                    continue
                expr = quicksum(terms)
            else:
                col_vars = [z[cid] for cid in constraint.column_ids if cid in z]
                if not col_vars:
                    continue
                expr = quicksum(col_vars)

            if constraint.operator == "==":
                cqm.add_constraint(expr == constraint.rhs, **con_kwargs)
            elif constraint.operator == "<=":
                cqm.add_constraint(expr <= constraint.rhs, **con_kwargs)
            elif constraint.operator == ">=":
                cqm.add_constraint(expr >= constraint.rhs, **con_kwargs)
            extra_count += 1
            _mode = "soft" if constraint.is_soft else "hard"
            logger.info(f"CQM ({_mode}): {constraint.label}")

        logger.info(
            f"CQM constraints: mode={constraint_mode}, "
            f"weight={'hard' if structural_weight is None else structural_weight}, "
            f"coverage={coverage_count}, extra={extra_count}"
        )

        compile_time = time.time() - t0
        total_constraints = coverage_count + extra_count

        # ── SP 진단 정보 (problem 구축 시 이미 생성됨) ──
        sp_diagnostics = problem.diagnostics

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
                "all_task_ids": problem.task_ids,
                "compile_time": compile_time,
                "sp_diagnostics": sp_diagnostics,
            },
        )


# CQMExecutor는 engine/executor/cqm_executor.py로 이동 (GR-1 아키텍처 정리)
# 하위 호환: 기존 import 경로 유지
from engine.executor.cqm_executor import CQMExecutor  # noqa: F401
