"""
Stage 5 / Stage 6 — 거점(Depot) 검증기.

거점 정책과 실제 데이터의 정합성을 검증합니다.
엔진은 depot 이름의 의미를 해석하지 않습니다 (opaque label).

포함 검증기:
  - DepotPolicyValidator (Stage 5, pre-solve):
      depot_policy가 active인데 task에 depot이 할당되지 않은 경우 경고/차단
  - DepotSolutionValidator (Stage 6, post-solve):
      솔루션에서 cross-depot column이 발생한 경우 경고

기대하는 context 키:
    Stage 5:
        tasks: List[TaskItem]       — depot이 resolve된 task 목록
        depot_policy: dict          — {"type": "single", ...}
        column_count: int           — 생성된 column 수 (선택)
    Stage 6:
        columns: List[FeasibleColumn] — 솔루션에 선택된 column 목록
        tasks: List[TaskItem]       — task 목록 (depot 정보 포함)
        depot_policy: dict          — {"type": "single", ...}
"""

from __future__ import annotations

import logging
from collections import Counter

from engine.validation.base import BaseValidator, ValidationResult

logger = logging.getLogger(__name__)


class DepotPolicyValidator(BaseValidator):
    """Stage 5 (pre-solve): depot 정책과 데이터의 정합성 검증.

    depot_policy가 active(single/hybrid)인데 실제 task에
    depot이 할당되지 않으면 정책이 무력화됨 → 경고.
    """

    stage = 5
    name = "DepotPolicyValidator"
    description = "거점 정책-데이터 정합성 검증"

    def validate(self, context: dict) -> ValidationResult:
        result = self._make_result()

        policy = context.get("depot_policy") or {}
        policy_type = policy.get("type", "multi")

        # multi 정책이면 검증 불필요
        if policy_type == "multi":
            return result

        tasks = context.get("tasks") or []
        if not tasks:
            return result

        # ── 1. wildcard task 비율 확인 ──────────────────────
        wildcard_count = sum(1 for t in tasks if not t.allowed_depots)
        total = len(tasks)
        wildcard_ratio = wildcard_count / total if total > 0 else 0

        if wildcard_count == total:
            result.add_error(
                code="DEPOT_POLICY_NO_DATA",
                message=(
                    f"거점 정책이 '{policy_type}'이지만 "
                    f"모든 trip({total}개)에 거점이 할당되지 않았습니다."
                ),
                suggestion=(
                    "CSV에 'depot' 컬럼을 추가하거나, "
                    "문제 정의에서 거점 매핑(depots)을 설정하세요."
                ),
                detail=(
                    "거점 미할당 상태에서는 정책이 무력화되어 "
                    "거점 간 혼합 duty가 생성될 수 있습니다."
                ),
            )
        elif wildcard_ratio > 0.3:
            result.add_warning(
                code="DEPOT_HIGH_WILDCARD_RATIO",
                message=(
                    f"거점 정책이 '{policy_type}'이지만 "
                    f"trip의 {wildcard_ratio:.0%}({wildcard_count}/{total})에 "
                    f"거점이 할당되지 않았습니다."
                ),
                suggestion=(
                    "거점 매핑에 누락된 역이 있는지 확인하세요. "
                    "미할당 trip은 어떤 거점의 duty에든 배정될 수 있습니다."
                ),
            )

        # ── 2. depot 분포 진단 ─────────────────────────────
        depot_counts = Counter(d for t in tasks for d in t.allowed_depots)
        if depot_counts:
            result.add_info(
                code="DEPOT_DISTRIBUTION",
                message=f"거점별 trip 분포: {dict(depot_counts)}",
                context={
                    "depot_counts": dict(depot_counts),
                    "wildcard_count": wildcard_count,
                    "total_tasks": total,
                },
            )

        # ── 3. depot_source 추적 ──────────────────────────
        source_counts = Counter()
        for t in tasks:
            if getattr(t, 'raw_depot', ''):
                source_counts["csv"] += 1
            elif t.allowed_depots:
                source_counts["params"] += 1
            else:
                source_counts["wildcard"] += 1

        if source_counts:
            result.add_info(
                code="DEPOT_SOURCE_TRACE",
                message=(
                    f"거점 데이터 소스: "
                    f"CSV={source_counts.get('csv', 0)}, "
                    f"params={source_counts.get('params', 0)}, "
                    f"wildcard={source_counts.get('wildcard', 0)}"
                ),
                context={"source_counts": dict(source_counts)},
            )

        return result


class DepotSolutionValidator(BaseValidator):
    """Stage 6 (post-solve): 솔루션의 거점 분리 검증.

    solved column에서 cross-depot duty가 발생하면 경고.
    """

    stage = 6
    name = "DepotSolutionValidator"
    description = "솔루션 거점 분리 검증"

    def validate(self, context: dict) -> ValidationResult:
        result = self._make_result()

        policy = context.get("depot_policy") or {}
        policy_type = policy.get("type", "multi")

        if policy_type == "multi":
            return result

        columns = context.get("columns") or []
        tasks = context.get("tasks") or []
        if not columns or not tasks:
            return result

        task_map = {t.id: t for t in tasks}

        # ── cross-depot column 감지 ───────────────────────
        incompatible_count = 0
        incompatible_samples = []

        for col in columns:
            # column 내 모든 non-wildcard trip의 depot 교집합
            resolved = None
            for tid in col.trips:
                task = task_map.get(tid)
                if not task or not task.allowed_depots:
                    continue
                if resolved is None:
                    resolved = set(task.allowed_depots)
                else:
                    resolved &= task.allowed_depots

            if resolved is not None and len(resolved) == 0:
                incompatible_count += 1
                if len(incompatible_samples) < 3:
                    trip_depots = {
                        tid: list(task_map[tid].allowed_depots)
                        for tid in col.trips
                        if tid in task_map and task_map[tid].allowed_depots
                    }
                    incompatible_samples.append({
                        "column_id": col.id,
                        "start_depot": col.start_depot,
                        "trip_depots": trip_depots,
                    })

        # ── round-trip depot 검증 (출근=퇴근) ─────────────
        round_trip_violations = 0
        if policy.get("enforce_round_trip_depot", True):
            for col in columns:
                if col.start_depot and col.end_depot and col.start_depot != col.end_depot:
                    round_trip_violations += 1

            if round_trip_violations > 0:
                result.add_warning(
                    code="DEPOT_ROUND_TRIP_VIOLATION",
                    message=(
                        f"출근 거점 ≠ 퇴근 거점인 duty가 "
                        f"{round_trip_violations}개 있습니다."
                    ),
                    suggestion="거점 매핑 또는 overnight 설정을 확인하세요.",
                    context={"round_trip_violations": round_trip_violations},
                )

        if incompatible_count > 0:
            result.add_error(
                code="DEPOT_CROSS_DEPOT_SOLUTION",
                message=(
                    f"솔루션에 거점 간 혼합 duty가 {incompatible_count}개 발견되었습니다."
                ),
                detail=(
                    f"거점 정책이 '{policy_type}'이지만 "
                    f"서로 다른 거점의 trip이 같은 duty에 배정되었습니다."
                ),
                suggestion="거점 매핑 또는 column generator 설정을 확인하세요.",
                context={"incompatible_count": incompatible_count,
                         "samples": incompatible_samples},
            )
        else:
            # depot 정책 준수 확인
            depot_duty_counts = Counter()
            for col in columns:
                depot_duty_counts[col.start_depot or "unknown"] += 1

            if depot_duty_counts:
                result.add_info(
                    code="DEPOT_SOLUTION_OK",
                    message=f"거점별 duty 분포: {dict(depot_duty_counts)}",
                    context={"depot_duty_counts": dict(depot_duty_counts)},
                )

        return result
