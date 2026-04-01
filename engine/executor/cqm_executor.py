"""
cqm_executor.py ────────────────────────────────────────────
D-Wave LeapHybridCQMSampler 실행기.

CQM 컴파일 결과를 D-Wave에서 실행하고, coverage 검증 + repair 수행.
solver capacity/time_limit은 런타임에서 조회 (하드코딩 금지).

GR-1: engine 내부 모듈. domain import 없음.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from engine.compiler.base import CompileResult
from engine.executor.base import ExecuteResult

logger = logging.getLogger(__name__)


class CQMExecutor:
    """
    D-Wave LeapHybridCQMSampler 실행기.

    solver capacity/time_limit은 런타임에서 조회 (하드코딩 금지).
    """

    def execute(
        self,
        compile_result: CompileResult,
        time_limit_sec: Optional[int] = None,
        skip_repair: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """CQM 모델을 D-Wave에서 실행.

        Args:
            skip_repair: True면 post-solve repair를 건너뜀.
                Hybrid 모드에서 CP-SAT이 exact partition을 보장하므로
                CQM raw solution을 hint로 사용할 때 활성화.
        """
        from dwave.system import LeapHybridCQMSampler

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

        # ── 결과 해석: best sample 사용 (feasible 우선, 없으면 best overall) ──
        # NOTE: sampleset.filter()/.first 접근 시 D-Wave 결과 resolve (blocking)
        feasible = sampleset.filter(lambda s: s.is_feasible)
        feasible_count = len(feasible)
        wall_time = time.time() - t0  # resolve 완료 후 측정 (실제 대기 시간 포함)

        if feasible_count > 0:
            best = feasible.first
            logger.info(f"CQM: {feasible_count} feasible solutions found")
        else:
            # feasible 없으면 best sample (가장 적은 위반) 사용 → repair로 복구
            logger.warning(f"CQM: no feasible solutions, using best sample for repair")
            best = sampleset.first

        sample = best.sample

        # z 변수 추출 → solution dict (key는 str 통일 — converter 호환)
        solution = {"z": {}}
        for var_name, val in sample.items():
            if var_name.startswith("z_"):
                col_id = var_name[2:]  # "z_123" → "123" (str)
                solution["z"][str(col_id)] = int(val)

        objective_value = best.energy
        selected_count = sum(1 for v in solution["z"].values() if v > 0)

        # ── Post-solve: validate → repair (반복) → re-validate ──
        # Hybrid 모드(skip_repair=True)에서는 repair 건너뜀
        # → CP-SAT이 hard coverage(==1)로 exact partition 보장
        if skip_repair:
            logger.info(f"CQM: skip_repair=True, returning raw solution "
                        f"(selected={selected_count})")
            return ExecuteResult(
                success=True,
                solver_type="dwave_cqm",
                status="FEASIBLE_RAW",
                objective_value=objective_value,
                solution=solution,
                execution_time_sec=round(wall_time, 3),
                solver_info={
                    "sample_count": len(sampleset),
                    "feasible_count": feasible_count,
                    "selected_columns": selected_count,
                    "wall_time": wall_time,
                    "skip_repair": True,
                },
            )

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

        # D-Wave 서버 측 실행 시간 (과금 기준)
        # Hybrid solver: sampleset.info["charge_time"] (초 단위, top-level)
        dwave_timing = sampleset.info.get("timing", {})
        charge_time_s = sampleset.info.get("charge_time", 0)
        if not charge_time_s:
            charge_time_us = dwave_timing.get("charge_time", 0)
            charge_time_s = charge_time_us / 1_000_000 if charge_time_us else 0
        logger.info(f"D-Wave CQM: {status}, obj={objective_value:.2f}, "
                     f"selected={selected_count}, wall_time={wall_time:.1f}s, "
                     f"charge_time={charge_time_s:.1f}s, "
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
        """
        duty_map = compile_result.metadata.get("duty_map", {})
        z_solution = solution.get("z", {})

        coverage = defaultdict(int)
        task_to_selected = defaultdict(list)

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
                    for t in best.trips:
                        coverage[t] += 1
                        task_to_selected[t].append(str(best.id))
                    repaired_count += 1
                    logger.debug(f"Repair: added column {best.id} for uncovered task {tid}")

        # Case B: duplicate (count>1) → column 단위로 제거
        selected_cols = [
            (cid_str, duty_map.get(int(cid_str)))
            for cid_str, val in z_solution.items()
            if int(val) == 1 and int(cid_str) in duty_map
        ]
        selected_cols.sort(key=lambda x: x[1].cost, reverse=True)

        for cid_str, col in selected_cols:
            if z_solution.get(cid_str, 0) != 1:
                continue
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
        all_task_ids = set(compile_result.metadata.get("all_task_ids", []))
        if not all_task_ids:
            for col in duty_map.values():
                all_task_ids.update(col.trips)

        z_solution = solution.get("z", {})

        coverage = defaultdict(int)
        for col_id_str, val in z_solution.items():
            if int(val) == 1:
                col = duty_map.get(int(col_id_str))
                if col:
                    for tid in col.trips:
                        coverage[tid] += 1

        violations = {}
        for tid in all_task_ids:
            cnt = coverage.get(tid, 0)
            if cnt != 1:
                violations[tid] = cnt

        return violations
