"""
feasibility/builtin.py — Built-in feasibility check handlers
=============================================================
engine이 기본 제공하는 handler 세트.

모든 handler는 모듈 로딩 시 자동 등록됨.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from engine.feasibility.base import (
    FeasibilityCheck,
    CheckResult,
    FeasibilityCheckRegistry,
    resolve_param,
)

logger = logging.getLogger(__name__)


# ── max_value: 필드값 ≤ 상한 ────────────────────────────────

class MaxValueCheck(FeasibilityCheck):
    """column의 특정 필드가 상한 이하인지 검증.

    YAML 예시:
      - type: max_value
        field: idle_minutes
        limit_param: max_idle_time    # params에서 조회
        # 또는
        limit: 300                    # 직접 값
        action: reject
    """

    def check(self, column: Any, config: Dict[str, Any],
              params: Dict[str, Any]) -> CheckResult:
        field_name = config.get("field", "")
        value = getattr(column, field_name, None)
        if value is None:
            return CheckResult(feasible=True, reason=f"field '{field_name}' not found, skip")

        limit = resolve_param(config, "limit", params)
        if limit is None:
            return CheckResult(feasible=True, reason=f"no limit for '{field_name}', skip")

        limit = float(limit)
        value = float(value)

        if value > limit:
            return CheckResult(
                feasible=False,
                reason=f"{field_name}={value:.0f} > {limit:.0f}",
            )
        return CheckResult(feasible=True)


# ── min_value: 필드값 ≥ 하한 ────────────────────────────────

class MinValueCheck(FeasibilityCheck):
    """column의 특정 필드가 하한 이상인지 검증.

    YAML 예시:
      - type: min_value
        field: active_minutes
        limit_param: min_active_time
        action: reject
    """

    def check(self, column: Any, config: Dict[str, Any],
              params: Dict[str, Any]) -> CheckResult:
        field_name = config.get("field", "")
        value = getattr(column, field_name, None)
        if value is None:
            return CheckResult(feasible=True, reason=f"field '{field_name}' not found, skip")

        limit = resolve_param(config, "limit", params)
        if limit is None:
            return CheckResult(feasible=True, reason=f"no limit for '{field_name}', skip")

        limit = float(limit)
        value = float(value)

        if value < limit:
            return CheckResult(
                feasible=False,
                reason=f"{field_name}={value:.0f} < {limit:.0f}",
            )
        return CheckResult(feasible=True)


# ── break_window: 시간 구간 내 최소 연속 공백 ───────────────

class BreakWindowCheck(FeasibilityCheck):
    """특정 시간 구간에 최소 연속 공백이 있는지 검증.

    "11:00~14:00 사이에 최소 30분 연속 공백" 같은 식사 휴게시간 보장.
    column.trips (task_id 목록)에서 해당 시간대의 gap을 분석.

    YAML 예시:
      - type: break_window
        windows:
          - start_param: meal_window_start    # 660 (11:00)
            end_param: meal_window_end        # 840 (14:00)
            min_gap_param: min_meal_break_minutes  # 30
            start: 660                        # fallback
            end: 840
            min_gap: 30
        action: reject

    column에 trips의 시각 정보가 필요하므로, task_map을 params['_task_map']으로 전달받음.
    """

    def check(self, column: Any, config: Dict[str, Any],
              params: Dict[str, Any]) -> CheckResult:
        windows = config.get("windows", [])
        if not windows:
            return CheckResult(feasible=True)

        task_map = params.get("_task_map", {})
        if not task_map:
            # task_map 미제공 시 skip (경고 없이 — 초기 단계에서는 미지원 가능)
            return CheckResult(feasible=True, reason="no _task_map, skip")

        trips = getattr(column, "trips", [])
        if not trips:
            return CheckResult(feasible=True)

        # trip 시각 정보 추출: [(dep_time, arr_time), ...]
        trip_times = []
        for tid in trips:
            task = task_map.get(tid)
            if task:
                trip_times.append((task.dep_time, task.arr_time))
        trip_times.sort(key=lambda x: x[0])

        for win_cfg in windows:
            win_start = resolve_param(win_cfg, "start", params)
            win_end = resolve_param(win_cfg, "end", params)
            min_gap = resolve_param(win_cfg, "min_gap", params)

            if win_start is None or win_end is None or min_gap is None:
                continue

            win_start = int(win_start)
            win_end = int(win_end)
            min_gap = int(min_gap)

            max_gap = self._find_max_gap_in_window(
                trip_times, win_start, win_end, column
            )

            if max_gap < min_gap:
                return CheckResult(
                    feasible=False,
                    reason=(
                        f"break_window [{win_start}..{win_end}]: "
                        f"max_gap={max_gap}min < required={min_gap}min"
                    ),
                )

        return CheckResult(feasible=True)

    @staticmethod
    def _find_max_gap_in_window(
        trip_times: List[tuple], win_start: int, win_end: int,
        column: Any,
    ) -> int:
        """시간 구간 [win_start, win_end] 내에서 가장 긴 연속 공백을 찾음."""
        # column의 start/end 시각
        col_start = getattr(column, "start_time", 0)
        col_end = getattr(column, "end_time", 0)

        # window가 column 시간대 밖이면 → 전체가 공백
        if col_end <= win_start or col_start >= win_end:
            return win_end - win_start

        # window 내의 "사용 중인" 시간 구간 수집
        occupied = []
        for dep, arr in trip_times:
            # window와 겹치는 구간만
            seg_start = max(dep, win_start)
            seg_end = min(arr, win_end)
            if seg_start < seg_end:
                occupied.append((seg_start, seg_end))

        if not occupied:
            return win_end - win_start

        # 겹치는 구간 병합
        occupied.sort()
        merged = [occupied[0]]
        for seg_start, seg_end in occupied[1:]:
            if seg_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], seg_end))
            else:
                merged.append((seg_start, seg_end))

        # gap 계산: window 시작 → 첫 occupied, occupied 간, 마지막 occupied → window 끝
        max_gap = 0
        prev_end = win_start
        for seg_start, seg_end in merged:
            gap = seg_start - prev_end
            max_gap = max(max_gap, gap)
            prev_end = seg_end
        # 마지막 occupied → window 끝
        max_gap = max(max_gap, win_end - prev_end)

        return max_gap


# ── min_turnaround: 연속 trip 간 최소 간격 ──────────────────

class MinTurnaroundCheck(FeasibilityCheck):
    """연속 trip 간 최소 간격(강차 후 휴양시간) 검증.

    YAML 예시:
      - type: min_turnaround
        min_gap_param: post_arrival_rest_minutes_min  # 60분
        min_gap: 60                                    # fallback
        action: reject

    task_map을 params['_task_map']으로 전달받음.
    """

    def check(self, column: Any, config: Dict[str, Any],
              params: Dict[str, Any]) -> CheckResult:
        min_gap = resolve_param(config, "min_gap", params)
        if min_gap is None or int(min_gap) <= 0:
            return CheckResult(feasible=True, reason="no min_gap, skip")

        min_gap = int(min_gap)

        task_map = params.get("_task_map", {})
        if not task_map:
            return CheckResult(feasible=True, reason="no _task_map, skip")

        trips = getattr(column, "trips", [])
        if len(trips) < 2:
            return CheckResult(feasible=True)

        # 시간순으로 trip 간 gap 체크
        for i in range(len(trips) - 1):
            task_a = task_map.get(trips[i])
            task_b = task_map.get(trips[i + 1])
            if not task_a or not task_b:
                continue

            gap = task_b.dep_time - task_a.arr_time
            if gap < min_gap:
                return CheckResult(
                    feasible=False,
                    reason=(
                        f"turnaround gap={gap}min < min={min_gap}min "
                        f"(trip {trips[i]}→{trips[i+1]})"
                    ),
                )

        return CheckResult(feasible=True)


# ── 자동 등록 ───────────────────────────────────────────────

def register_builtin_handlers():
    """built-in handler를 registry에 등록. 모듈 import 시 자동 호출."""
    FeasibilityCheckRegistry.register("max_value", MaxValueCheck)
    FeasibilityCheckRegistry.register("min_value", MinValueCheck)
    FeasibilityCheckRegistry.register("break_window", BreakWindowCheck)
    FeasibilityCheckRegistry.register("min_turnaround", MinTurnaroundCheck)


# 모듈 로딩 시 자동 등록
register_builtin_handlers()
