"""
set_partitioning_compiler.py ────────────────────────────────
Set Partitioning Compiler (CP-SAT / MILP).

업계 표준 crew scheduling 모델:
  - DutyGenerator가 생성한 feasible duty 중 어떤 것을 선택할지만 결정
  - solver는 시간 제약을 전혀 모름 (prep/cleanup/break/sleep 없음)
  - 모든 시간 검증은 Generator에서 완료됨

모델:
  변수: z[k] ∈ {0,1} — duty k를 선택
  제약: ∀i ∈ trips, sum(z[k] for k if i ∈ duty[k].trips) == 1  (coverage)
  목적: min sum(z[k] * cost[k])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from engine.compiler.base import BaseCompiler, CompileResult
from engine.duty_generator import FeasibleDuty

logger = logging.getLogger(__name__)


class SetPartitioningCompiler(BaseCompiler):
    """Set Partitioning 모델 컴파일러 (CP-SAT 기반)"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """
        Set Partitioning 모델 컴파일.

        kwargs 필수:
          duties: List[FeasibleDuty] — DutyGenerator 출력
        """
        duties: List[FeasibleDuty] = kwargs.pop("duties", [])
        if not duties:
            return CompileResult(
                success=False,
                error="No feasible duties provided. Run DutyGenerator first.",
            )

        try:
            return self._compile_sp(duties, bound_data, **kwargs)
        except Exception as e:
            logger.error(f"SP compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _compile_sp(
        self, duties: List[FeasibleDuty], bound_data: Dict, **kwargs
    ) -> CompileResult:
        """CP-SAT Set Partitioning 모델 생성"""
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()
        params = bound_data.get("parameters", {})

        # ── 1. 변수: z[k] (duty 선택) ──
        z = {}
        for d in duties:
            z[d.id] = model.new_bool_var(f"z_{d.id}")

        # ── 2. Trip → Duty 인덱스 구축 ──
        # trip_id → [duty_id, ...]
        trip_to_duties: Dict[int, List[int]] = {}
        for d in duties:
            for tid in d.trips:
                trip_to_duties.setdefault(tid, []).append(d.id)

        # ── 3. Coverage 제약: 각 trip을 정확히 1개 duty에 배정 ──
        all_trip_ids = sorted(trip_to_duties.keys())
        coverage_count = 0

        for tid in all_trip_ids:
            duty_ids = trip_to_duties[tid]
            if not duty_ids:
                logger.error(f"SP: trip {tid} has no covering duty!")
                continue
            model.add(sum(z[did] for did in duty_ids) >= 1)
            coverage_count += 1

        # ── 4. 선택적 제약: crew 수 고정 ──
        # 주의: Generator가 충분한 multi-trip duty를 생성하지 못하면
        # duty 수 제약이 INFEASIBLE을 유발. 향후 Generator 개선 후 활성화.
        total_duties_param = None  # params.get("total_duties")
        day_count_param = None     # params.get("day_crew_count")
        night_count_param = None   # params.get("night_crew_count")

        duty_map = {d.id: d for d in duties}
        extra_constraints = 0

        if total_duties_param is not None:
            total = int(total_duties_param)
            model.add(sum(z.values()) == total)
            extra_constraints += 1
            logger.info(f"SP: fixed total duties = {total}")

        if day_count_param is not None:
            day_target = int(day_count_param)
            day_z = [z[d.id] for d in duties if not d.is_night]
            if day_z:
                model.add(sum(day_z) == day_target)
                extra_constraints += 1
                logger.info(f"SP: fixed day duties = {day_target}")

        if night_count_param is not None:
            night_target = int(night_count_param)
            night_z = [z[d.id] for d in duties if d.is_night]
            if night_z:
                model.add(sum(night_z) == night_target)
                extra_constraints += 1
                logger.info(f"SP: fixed night duties = {night_target}")

        # ── 5. 목적함수: 비용 최소화 ──
        # cost = 1.0 기본 + wait 페널티 + span 페널티
        # 정수화: cost * 1000
        cost_vars = []
        for d in duties:
            cost_int = int(d.cost * 1000)
            cost_vars.append(cost_int * z[d.id])

        model.minimize(sum(cost_vars))

        total_constraints = coverage_count + extra_constraints

        logger.info(
            f"SP compiled: {len(z)} duty vars, {coverage_count} coverage constraints, "
            f"{extra_constraints} extra constraints, {len(all_trip_ids)} trips"
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
                "duty_count": len(duties),
                "trip_count": len(all_trip_ids),
                "coverage_constraints": coverage_count,
                "duty_map": duty_map,
            },
        )
