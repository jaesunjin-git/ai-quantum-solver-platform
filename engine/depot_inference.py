"""
engine/depot_inference.py — 거점(Depot) 자동 추론 (Problem Layer)
================================================================
업로드 데이터에서 거점 이름을 감지하고, trip→depot 매핑을 추론.

설계 원칙:
  - 하드코딩 로직 없음 (패턴 기반 추론)
  - "공용" 개념 없음 → 허용 depot 집합(set)으로 표현
  - confidence 포함 → UI에서 자동/수동 분기
  - 엔진은 depot 이름의 의미를 해석하지 않음 (opaque label)

Usage:
    result = infer_depot_mapping(trips, depot_names, terminal_stations)
    # result.mapping: {trip_id: frozenset({"노포", "신평"})}
    # result.confidence: 0.87
    # result.summary: {"신평": 23, "노포,신평": 345}
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class DepotInferenceResult:
    """거점 추론 결과"""
    # 감지된 거점 이름
    depot_names: List[str] = field(default_factory=list)

    # trip_id → allowed_depots 매핑
    mapping: Dict[int, FrozenSet[str]] = field(default_factory=dict)

    # 추론 신뢰도 (0.0 ~ 1.0)
    confidence: float = 0.0

    # 거점별 trip 수 요약 (표시용)
    summary: Dict[str, int] = field(default_factory=dict)

    # 추론 방법 설명 (UI 표시용)
    method: str = ""

    # 경고/참고 메시지
    warnings: List[str] = field(default_factory=list)


def detect_depot_names(
    parameters: List[Dict],
    dataframes: Optional[Dict] = None,
) -> List[str]:
    """업로드 데이터에서 거점 이름을 감지.

    감지 소스 (우선순위):
      1. parameter 시트에서 거점별 컬럼 헤더 (예: "전체, 노포, 신평")
      2. dataframes에서 거점 관련 시트 컬럼

    Returns:
        거점 이름 리스트 (빈 리스트 = 감지 불가)
    """
    depots: List[str] = []

    # 소스 1: parameters에서 근무인원/사업 관련 행의 context 파싱
    # context에 공백 구분 숫자들이 있고, 원본 시트 헤더에 거점 이름이 있음
    crew_keywords = {"근무인원", "사업", "crew", "인원"}

    if dataframes:
        for sheet_name, df in dataframes.items():
            # 시트 이름에 근무/인원 키워드 포함 + 컬럼에 숫자형이 아닌 문자열 헤더
            if not any(k in sheet_name for k in crew_keywords):
                continue

            cols = [str(c) for c in df.columns]
            # "전체" 컬럼 제외, "구분" 제외, 숫자가 아닌 컬럼 = 거점 후보
            skip_names = {"구분", "전체", "합계", "항목", "비고"}
            candidates = [
                c for c in cols
                if c not in skip_names and not c.replace('.', '').isdigit()
            ]
            if len(candidates) >= 2:
                depots = candidates
                logger.info(
                    f"Depot names detected from sheet '{sheet_name}': {depots}"
                )
                break

    if not depots:
        logger.info("No depot names detected from uploaded data")

    return depots


def infer_depot_mapping(
    trips: list,
    depot_names: List[str],
) -> DepotInferenceResult:
    """trip→depot 매핑을 자동 추론.

    전략: 거점 이름이 역 이름과 일치하면, 해당 역을 포함하는 trip은
    그 거점에 할당. 어떤 거점 역도 포함하지 않는 trip은 전체 허용.

    Args:
        trips: TaskItem 리스트 (start_location, end_location 필요)
        depot_names: 감지된 거점 이름 리스트

    Returns:
        DepotInferenceResult (mapping, confidence, summary)
    """
    result = DepotInferenceResult()

    if not depot_names or len(depot_names) < 2:
        result.method = "no_depots"
        result.confidence = 0.0
        result.warnings.append("거점이 2개 미만으로 감지되어 매핑을 생성하지 않습니다.")
        return result

    result.depot_names = list(depot_names)

    # 전체 역 목록 추출
    all_stations: Set[str] = set()
    for t in trips:
        all_stations.add(t.start_location)
        all_stations.add(t.end_location)

    # 거점 이름 ↔ 역 이름 매칭 (depot 이름이 역 이름에 포함되는지)
    depot_stations: Dict[str, Set[str]] = {d: set() for d in depot_names}
    for depot in depot_names:
        for station in all_stations:
            # 역 이름에 거점 이름이 포함 (예: "신평" in "신평", "신평기지")
            if depot in station:
                depot_stations[depot].add(station)

    # 매칭 결과 로깅
    matched_depots = {d: s for d, s in depot_stations.items() if s}
    unmatched_depots = [d for d in depot_names if not depot_stations[d]]

    logger.info(f"Depot-station matching: {matched_depots}")
    if unmatched_depots:
        logger.warning(f"Depots with no matching stations: {unmatched_depots}")
        result.warnings.append(
            f"역 이름과 매칭되지 않는 거점: {unmatched_depots}"
        )

    # trip → allowed_depots 매핑
    all_depot_set = frozenset(depot_names)
    depot_specific_count = 0
    multi_depot_count = 0

    summary_counter: Counter = Counter()

    for t in trips:
        # 이 trip이 어떤 거점의 역을 포함하는지
        trip_depots: Set[str] = set()
        for depot, stations in depot_stations.items():
            if t.start_location in stations or t.end_location in stations:
                trip_depots.add(depot)

        if trip_depots and trip_depots != set(depot_names):
            # 특정 거점에만 해당
            allowed = frozenset(trip_depots)
            depot_specific_count += 1
        else:
            # 어떤 특정 거점에도 매칭 안 되거나, 모든 거점에 매칭 → 전체 허용
            allowed = all_depot_set
            multi_depot_count += 1

        result.mapping[t.id] = allowed
        # summary key: 정렬된 depot 이름 결합
        key = ",".join(sorted(allowed))
        summary_counter[key] += 1

    result.summary = dict(summary_counter)

    # confidence 계산
    total = len(trips)
    if total == 0:
        result.confidence = 0.0
    else:
        # 패턴 일관성: 특정 거점 할당 비율이 합리적 범위인지
        specific_ratio = depot_specific_count / total
        has_matching = len(matched_depots) >= 2
        no_conflicts = len(unmatched_depots) == 0

        if has_matching and specific_ratio > 0.01:
            # 정상: 거점 매칭 존재 + 일부 trip이 특정 거점
            result.confidence = 0.85 if no_conflicts else 0.7
        elif has_matching:
            # 매칭은 있지만 모든 trip이 공용
            result.confidence = 0.5
            result.warnings.append(
                "모든 trip이 전체 거점에 해당합니다. 매핑이 부정확할 수 있습니다."
            )
        else:
            result.confidence = 0.3

    result.method = "station_name_matching"

    logger.info(
        f"Depot inference: {len(depot_names)} depots, "
        f"{depot_specific_count} specific + {multi_depot_count} multi = {total} trips, "
        f"confidence={result.confidence:.2f}, "
        f"summary={result.summary}"
    )

    return result


def format_depot_inference_for_ui(result: DepotInferenceResult) -> str:
    """추론 결과를 사용자 표시용 텍스트로 포맷.

    Problem Definition에서 사용자에게 보여주는 확인 메시지.
    """
    if not result.depot_names:
        return ""

    lines = [f"**감지된 거점**: {', '.join(result.depot_names)}"]
    lines.append("")

    for key, count in sorted(result.summary.items()):
        depots = key.split(",")
        if len(depots) == 1:
            lines.append(f"- {depots[0]} 전용: {count}개 trip")
        else:
            lines.append(f"- {'/'.join(depots)} 공용: {count}개 trip")

    conf_label = (
        "높음" if result.confidence >= 0.8
        else "중간" if result.confidence >= 0.5
        else "낮음"
    )
    lines.append(f"\n신뢰도: {conf_label} ({result.confidence:.0%})")

    if result.warnings:
        lines.append("")
        for w in result.warnings:
            lines.append(f"⚠️ {w}")

    return "\n".join(lines)
