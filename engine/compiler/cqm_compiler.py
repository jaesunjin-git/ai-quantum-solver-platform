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

        valid, errors, warnings = sp_problem.validate()
        for w in warnings:
            logger.warning(f"CQM: {w}")
        if not valid:
            return CompileResult(
                success=False,
                error=f"SP problem invalid: {'; '.join(errors)}",
                metadata={"sp_diagnostics": sp_problem.diagnostics},
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
    import os as _os
    CQM_MAX_COLUMNS = int(_os.environ.get("CQM_MAX_COLUMNS", 20000))

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

        # ── 0. Column cap: CQM은 대규모 변수에 느리므로 제한 ──
        # coverage 보장: 모든 task를 커버하는 column은 cap에서 보호
        columns = problem.columns
        if len(columns) > self.CQM_MAX_COLUMNS:
            columns = self._cap_with_coverage(
                problem.columns, problem.task_to_columns, problem.task_ids,
                self.CQM_MAX_COLUMNS
            )
            logger.info(f"CQM: column cap {len(problem.columns)} → {len(columns)}")

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

        # ── 2. Coverage 제약: soft ==1 (#1) ──
        # CQM은 hard ==1에서 feasible 못 찾을 수 있으므로
        # soft constraint(weight=1000)로 설정 → solver가 trade-off
        # repair는 여전히 보험으로 유지
        coverage_count = 0
        for tid in problem.task_ids:
            col_ids = task_to_columns.get(tid, [])
            if not col_ids:
                logger.error(f"CQM: task {tid} has no covering column!")
                continue
            cqm.add_constraint(
                quicksum(z[cid] for cid in col_ids) == 1,
                label=f"cover_{tid}",
                weight=1000,  # soft: 위반 시 penalty
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

        # quicksum으로 objective 구축 (#3: 정규화 스케일링)
        max_score = max(scores.values(), default=1000)
        obj_terms = [
            (scores.get(col.id, max_score) / max(max_score, 1)) * z[col.id]
            for col in columns
        ]
        cqm.set_objective(quicksum(obj_terms))

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

        # ── time_limit: 런타임 조회, 기본 180초 (600초는 과도) ──
        default_limit = 180
        min_time = sampler.min_time_limit(cqm)
        user_limit = time_limit_sec or default_limit
        effective_limit = max(user_limit, min_time)
        if user_limit < min_time:
            logger.warning(f"CQM: time_limit {user_limit}s < min_time_limit {min_time}s, "
                          f"using {min_time}s")

        logger.info(f"D-Wave CQM: submitting (time_limit={effective_limit}s)")

        # ── 실행 ──
        t0 = time.time()
        try:
            sampleset = sampler.sample_cqm(cqm, time_limit=effective_limit)
        except Exception as e:
            wall_time = time.time() - t0
            logger.error(f"D-Wave API call failed: {e}", exc_info=True)
            return ExecuteResult(
                success=False, solver_type="dwave_cqm", status="ERROR",
                error=f"D-Wave API call failed: {e}",
                execution_time_sec=round(wall_time, 3),
            )
        wall_time = time.time() - t0

        # ── 결과 해석: best sample 사용 (feasible 우선, 없으면 best overall) ──
        feasible = sampleset.filter(lambda s: s.is_feasible)
        feasible_count = len(feasible)

        if feasible_count > 0:
            best = feasible.first
            logger.info(f"CQM: {feasible_count} feasible solutions found")
        else:
            # feasible 없으면 best sample (가장 적은 위반) 사용 → repair로 복구
            logger.warning(f"CQM: no feasible solutions, using best sample for repair")
            best = sampleset.first

        sample = best.sample

        # z 변수 추출 → solution dict (#5: key는 str 통일 — converter 호환)
        solution = {"z": {}}
        for var_name, val in sample.items():
            if var_name.startswith("z_"):
                col_id = var_name[2:]  # "z_123" → "123" (str)
                solution["z"][str(col_id)] = int(val)

        objective_value = best.energy
        selected_count = sum(1 for v in solution["z"].values() if v > 0)

        # ── Post-solve: validate → repair (반복) → re-validate ──
        violations_before = self._validate_coverage(solution, compile_result)
        repaired = False

        if violations_before:
            logger.info(f"CQM repair: {len(violations_before)} violations before repair "
                        f"(uncovered={sum(1 for c in violations_before.values() if c == 0)}, "
                        f"duplicate={sum(1 for c in violations_before.values() if c > 1)})")
            # 반복 repair (최대 5회, 수렴까지)
            for repair_round in range(5):
                solution = self._repair_coverage(solution, compile_result)
                repaired = True
                v = self._validate_coverage(solution, compile_result)
                if not v:
                    logger.info(f"CQM repair: converged at round {repair_round + 1}")
                    break
                logger.info(f"CQM repair round {repair_round + 1}: {len(v)} remaining")

        violations_after = self._validate_coverage(solution, compile_result)
        selected_count = sum(1 for v in solution["z"].values() if v > 0)

        if violations_after:
            status = "INFEASIBLE_POST"
            uncov = {t: c for t, c in violations_after.items() if c == 0}
            dupl = {t: c for t, c in violations_after.items() if c > 1}
            logger.warning(
                f"CQM: {len(violations_after)} violations AFTER repair "
                f"(uncovered={len(uncov)}, duplicate={len(dupl)})"
            )
        elif repaired:
            status = "FEASIBLE_REPAIRED"
            logger.info(f"CQM: repaired successfully, {selected_count} columns selected")
        else:
            status = "FEASIBLE"

        logger.info(f"D-Wave CQM: {status}, obj={objective_value:.2f}, "
                     f"selected={selected_count}, wall_time={wall_time:.1f}s, "
                     f"feasible={feasible_count}/{len(sampleset)}, repaired={repaired}")

        return ExecuteResult(
            success=status in ("FEASIBLE", "FEASIBLE_REPAIRED"),
            solver_type="dwave_cqm",
            status="FEASIBLE" if status == "FEASIBLE_REPAIRED" else status,
            objective_value=objective_value,
            solution=solution,
            execution_time_sec=round(wall_time, 3),
            solver_info={
                "sample_count": len(sampleset),
                "feasible_count": feasible_count,
                "selected_columns": selected_count,
                "wall_time": wall_time,
                "timing": sampleset.info.get("timing", {}),
                "repaired": repaired,
                "violations_before_repair": len(violations_before) if violations_before else 0,
                "violations_after_repair": len(violations_after) if violations_after else 0,
                "violation_detail": {
                    "uncovered": [t for t, c in (violations_after or {}).items() if c == 0],
                    "duplicate": [t for t, c in (violations_after or {}).items() if c > 1],
                } if violations_after else None,
            },
        )

    @staticmethod
    def _repair_coverage(
        solution: Dict, compile_result: CompileResult
    ) -> Dict:
        """
        CQM 결과의 coverage 위반을 repair.

        Case A (uncovered, count=0): anchor column 추가
        Case B (duplicate, count>1): cost 높은 column 제거

        Returns:
            repaired solution dict
        """
        duty_map = compile_result.metadata.get("duty_map", {})
        z_solution = solution.get("z", {})

        from collections import defaultdict
        coverage = defaultdict(int)
        task_to_selected = defaultdict(list)  # task → [selected col_ids]

        for col_id_str, val in z_solution.items():
            if int(val) == 1:
                col = duty_map.get(int(col_id_str))
                if col:
                    for tid in col.trips:
                        coverage[tid] += 1
                        task_to_selected[tid].append(col_id_str)

        # task → all covering columns (선택 여부 무관)
        task_to_all_cols = defaultdict(list)
        for col_id, col in duty_map.items():
            for tid in col.trips:
                task_to_all_cols[tid].append(col)

        repaired_count = 0

        # Case A: uncovered (count=0) → 가장 cost 낮은 column 추가
        all_task_ids = set()
        for col in duty_map.values():
            all_task_ids.update(col.trips)

        for tid in all_task_ids:
            if coverage[tid] == 0:
                candidates = task_to_all_cols.get(tid, [])
                if candidates:
                    best = min(candidates, key=lambda c: c.cost)
                    z_solution[str(best.id)] = 1
                    # coverage 업데이트
                    for t in best.trips:
                        coverage[t] += 1
                        task_to_selected[t].append(str(best.id))
                    repaired_count += 1
                    logger.debug(f"Repair: added column {best.id} for uncovered task {tid}")

        # Case B: duplicate (count>1) → column 단위로 제거
        # 선택된 column을 cost 높은 순으로 정렬, 제거 가능하면 제거
        selected_cols = [
            (cid_str, duty_map.get(int(cid_str)))
            for cid_str, val in z_solution.items()
            if int(val) == 1 and int(cid_str) in duty_map
        ]
        # cost 높은 순 (제거 우선)
        selected_cols.sort(key=lambda x: x[1].cost, reverse=True)

        for cid_str, col in selected_cols:
            if z_solution.get(cid_str, 0) != 1:
                continue  # 이미 제거됨

            # 이 column의 모든 task가 다른 column으로도 커버되면 제거 가능
            # #4: remove 후 모든 task가 여전히 coverage >= 1 유지 확인
            safe_to_remove = all((coverage[t] - 1) >= 1 for t in col.trips)

            if safe_to_remove:
                z_solution[cid_str] = 0
                for t in col.trips:
                    coverage[t] -= 1
                repaired_count += 1

        if repaired_count > 0:
            logger.info(f"CQM repair: {repaired_count} operations applied")

        solution["z"] = z_solution
        return solution

    @staticmethod
    def _validate_coverage(
        solution: Dict, compile_result: CompileResult
    ) -> Dict[int, int]:
        """
        CQM 결과의 coverage ==1 검증.

        Returns:
            {trip_id: actual_count} — 위반 trip만 (count != 1, 0=uncovered)
        """
        duty_map = compile_result.metadata.get("duty_map", {})
        # 전체 task set: metadata에서 가져오거나 duty_map에서 추출
        all_task_ids = set(compile_result.metadata.get("all_task_ids", []))
        if not all_task_ids:
            for col in duty_map.values():
                all_task_ids.update(col.trips)

        z_solution = solution.get("z", {})

        from collections import defaultdict
        coverage = defaultdict(int)

        for col_id_str, val in z_solution.items():
            if int(val) == 1:
                col = duty_map.get(int(col_id_str))
                if col:
                    for tid in col.trips:
                        coverage[tid] += 1

        # 전체 task set 기반 검증: uncovered(0) + duplicate(>1) 모두 감지
        violations = {}
        for tid in all_task_ids:
            cnt = coverage.get(tid, 0)
            if cnt != 1:
                violations[tid] = cnt

        return violations
