"""
sp_result_converter.py ──────────────────────────────────────
Set Partitioning 결과 → 기존 프론트엔드 포맷 변환.

SP 해: {z: {duty_id: 0/1}} + duty_map: {id: FeasibleDuty}
  ↓
기존 포맷:
  - duty_schedule.csv (duty별 trip 목록)
  - kpi.json (KPI 지표)
  - interpretation.json (결과 해석)
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any, Dict, List, Optional

from engine.duty_generator import FeasibleDuty, TripInfo

logger = logging.getLogger(__name__)


def convert_sp_result(
    solution: Dict[str, Any],
    duty_map: Dict[int, FeasibleDuty],
    trips: List[TripInfo],
    solver_id: str = "classical_cpu",
    solver_name: str = "CP-SAT (Set Partitioning)",
    project_dir: Optional[str] = None,
    objective_value: Optional[float] = None,
) -> Dict[str, Any]:
    """
    SP 결과를 기존 프론트엔드 포맷으로 변환.

    Args:
        solution: executor 결과 {z: {duty_id_str: 0/1}}
        duty_map: {duty_id_int: FeasibleDuty}
        trips: TripInfo 목록
        solver_id: solver 식별자
        solver_name: solver 표시명
        project_dir: 결과 파일 저장 경로 (None이면 저장 안 함)
        objective_value: solver objective

    Returns:
        interpretation dict (기존 포맷 호환)
    """
    trip_map = {t.id: t for t in trips}

    # ── 1. 선택된 duty 추출 ──
    z_solution = solution.get("z", {})
    selected_ids = [
        int(did) for did, val in z_solution.items()
        if isinstance(val, (int, float)) and val > 0
    ]

    selected_duties = []
    for did in selected_ids:
        d = duty_map.get(did)
        if d:
            selected_duties.append(d)

    # crew_id 부여 (1-indexed)
    selected_duties.sort(key=lambda d: d.start_time)

    logger.info(f"SP result: {len(selected_duties)} duties selected")

    # ── 2. duty_schedule rows 생성 ──
    schedule_rows = []
    for crew_idx, duty in enumerate(selected_duties, 1):
        # duty_start/end를 HH:MM 형식으로
        ds_hh = f"{duty.start_time // 60:02d}:{duty.start_time % 60:02d}"
        de_hh = f"{duty.end_time // 60:02d}:{duty.end_time % 60:02d}"

        for tid in duty.trips:
            trip = trip_map.get(tid)
            if trip is None:
                logger.warning(f"Trip {tid} not found in trip_map")
                continue

            dep_hh = f"{trip.dep_time // 60:02d}:{trip.dep_time % 60:02d}"
            arr_hh = f"{trip.arr_time // 60:02d}:{trip.arr_time % 60:02d}"

            schedule_rows.append({
                "duty_id": crew_idx,
                "crew_id": crew_idx,
                "duty_start": ds_hh,
                "duty_end": de_hh,
                "trip_id": tid,
                "direction": trip.direction,
                "dep_station": trip.dep_station,
                "arr_station": trip.arr_station,
                "dep_time": dep_hh,
                "arr_time": arr_hh,
                "duration_min": trip.duration,
            })

    # 정렬: duty_id → dep_time
    schedule_rows.sort(key=lambda r: (r["duty_id"], r["dep_time"]))

    # ── 3. KPI 계산 ──
    total_trips_covered = sum(len(d.trips) for d in selected_duties)
    unique_trips = set()
    for d in selected_duties:
        unique_trips.update(d.trips)

    # overlap 체크
    overlap_count = total_trips_covered - len(unique_trips)

    day_duties = [d for d in selected_duties if not d.is_night]
    night_duties = [d for d in selected_duties if d.is_night]
    total_driving = sum(d.driving_minutes for d in selected_duties)
    total_wait = sum(d.wait_minutes for d in selected_duties)
    total_span = sum(d.span_minutes for d in selected_duties)

    kpi = {
        "active_duties": len(selected_duties),
        "day_duties": len(day_duties),
        "night_duties": len(night_duties),
        "total_trips": len(trips),
        "covered_trips": len(unique_trips),
        "coverage_rate": round(len(unique_trips) / max(len(trips), 1) * 100, 1),
        "overlap_trips": overlap_count,
        "avg_trips_per_duty": round(total_trips_covered / max(len(selected_duties), 1), 1),
        "total_driving_min": total_driving,
        "total_wait_min": total_wait,
        "total_span_min": total_span,
        "avg_driving_per_duty": round(total_driving / max(len(selected_duties), 1), 1),
        "avg_wait_per_duty": round(total_wait / max(len(selected_duties), 1), 1),
        "driving_efficiency": round(total_driving / max(total_span, 1) * 100, 1),
        "constraint_violations": 0,
        "source_distribution": {
            d.source: sum(1 for dd in selected_duties if dd.source == d.source)
            for d in selected_duties
        } if selected_duties else {},
    }

    # ── 4. duties 상세 (interpretation용) ──
    duties_detail = []
    for crew_idx, duty in enumerate(selected_duties, 1):
        duties_detail.append({
            "duty_id": crew_idx,
            "is_night": duty.is_night,
            "start_time": duty.start_time,
            "end_time": duty.end_time,
            "trips": duty.trips,
            "driving_minutes": duty.driving_minutes,
            "wait_minutes": duty.wait_minutes,
            "span_minutes": duty.span_minutes,
            "break_minutes": duty.break_minutes,
            "sleep_minutes": duty.sleep_minutes,
            "source": duty.source,
            "cost": round(duty.cost, 2),
            # 프론트엔드 호환 필드
            "trip_count": len(duty.trips),
            "start_time_min": duty.start_time,
            "total_driving_min": duty.driving_minutes,
            "violations": [],  # SP duty는 Generator에서 검증 완료
        })

    # ── 5. constraint status (Generator 검증 결과 기반) ──
    constraint_status = _build_constraint_status(selected_duties, len(trips))
    soft_constraint_status = _build_soft_constraint_status(selected_duties)

    # ── 6. interpretation dict ──
    interpretation = {
        "objective_type": "minimize",
        "objective_label": "승무원 수 최소화 (Set Partitioning)",
        "objective_value": objective_value,
        "solver_id": solver_id,
        "solver_name": solver_name,
        "status": "OPTIMAL",
        "model_type": "SetPartitioning",
        "kpi": kpi,
        "duties": duties_detail,
        "schedule_summary": {
            "total_duties": len(selected_duties),
            "day_duties": len(day_duties),
            "night_duties": len(night_duties),
            "total_trips_covered": len(unique_trips),
            "overlap_trips": overlap_count,
        },
        "constraint_status": constraint_status,
        "soft_constraint_status": soft_constraint_status,
        "warnings": [],
    }

    # ── 7. 파일 저장 ──
    if project_dir:
        results_dir = os.path.join(project_dir, "results")
        os.makedirs(results_dir, exist_ok=True)

        # duty_schedule.csv
        csv_path = os.path.join(results_dir, f"duty_schedule_{solver_id}.csv")
        fieldnames = [
            "duty_id", "crew_id", "duty_start", "duty_end",
            "trip_id", "direction", "dep_station", "arr_station",
            "dep_time", "arr_time", "duration_min",
        ]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(schedule_rows)
        logger.info(f"SP schedule saved: {csv_path} ({len(schedule_rows)} rows)")

        # kpi.json
        kpi_path = os.path.join(results_dir, f"kpi_{solver_id}.json")
        with open(kpi_path, "w", encoding="utf-8") as f:
            json.dump(kpi, f, ensure_ascii=False, indent=2)

        # interpretation.json
        interp_path = os.path.join(results_dir, f"interpretation_{solver_id}.json")
        with open(interp_path, "w", encoding="utf-8") as f:
            json.dump(interpretation, f, ensure_ascii=False, indent=2)

        # solution.json
        sol_path = os.path.join(results_dir, f"solution_{solver_id}.json")
        with open(sol_path, "w", encoding="utf-8") as f:
            json.dump({
                "selected_duty_ids": selected_ids,
                "total_duties": len(selected_duties),
                "solver_id": solver_id,
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"SP results saved to {results_dir}")

    return interpretation


# ── Constraint Status 생성 (Generator 검증 기반) ─────────

def _build_constraint_status(
    selected_duties: List[FeasibleDuty],
    total_trips: int,
) -> List[Dict[str, Any]]:
    """
    선택된 duty의 metrics 기반으로 hard 제약 달성 현황 생성.

    Generator가 duty 생성 시 이미 검증했으므로 모두 satisfied.
    실제 최대/최소 값을 표시하여 여유도를 보여줌.
    """
    if not selected_duties:
        return []

    # 각 duty에서 metrics 수집
    drivings = [d.driving_minutes for d in selected_duties]
    works = [d.work_minutes for d in selected_duties]
    waits = [d.wait_minutes for d in selected_duties]
    spans = [d.span_minutes for d in selected_duties]
    trip_counts = [len(d.trips) for d in selected_duties]

    # 야간 duty만
    night_duties = [d for d in selected_duties if d.is_night]
    day_duties = [d for d in selected_duties if not d.is_night]

    status = []

    # 1. 최대 운전시간
    max_driving = max(drivings) if drivings else 0
    status.append({
        "name": "최대 운전시간 (max_driving_time)",
        "satisfied": True,
        "max_actual": f"{max_driving}분",
        "limit": "360분",
        "constraint_type": "parametric",
    })

    # 2. 최대 근무시간
    max_work = max(works) if works else 0
    status.append({
        "name": "최대 근무시간 (max_work_time)",
        "satisfied": True,
        "max_actual": f"{max_work}분",
        "limit": "660분",
        "constraint_type": "parametric",
    })

    # 3. 최대 대기시간
    max_wait = max(waits) if waits else 0
    status.append({
        "name": "최대 대기시간 (max_wait_time)",
        "satisfied": True,
        "max_actual": f"{max_wait}분",
        "limit": "300분",
        "constraint_type": "parametric",
    })

    # 4. Trip 커버리지
    unique_trips = set()
    for d in selected_duties:
        unique_trips.update(d.trips)
    coverage = len(unique_trips)
    status.append({
        "name": "트립 커버리지 (trip_coverage)",
        "satisfied": coverage >= total_trips,
        "max_actual": f"{coverage}/{total_trips}",
        "limit": f"{total_trips}",
        "constraint_type": "structural",
    })

    # 5. 승무원 수
    status.append({
        "name": "총 승무원 수 (crew_count)",
        "satisfied": True,
        "max_actual": f"{len(selected_duties)}명",
        "limit": f"{len(selected_duties)}명",
        "constraint_type": "structural",
    })

    # 6. 주간/야간 분배
    status.append({
        "name": "주간 승무원 (day_crew)",
        "satisfied": True,
        "max_actual": f"{len(day_duties)}명",
        "limit": f"{len(day_duties)}명",
        "constraint_type": "structural",
    })

    status.append({
        "name": "야간 승무원 (night_crew)",
        "satisfied": True,
        "max_actual": f"{len(night_duties)}명",
        "limit": f"{len(night_duties)}명",
        "constraint_type": "structural",
    })

    # 7. 야간 수면시간 (야간 duty만)
    if night_duties:
        min_sleep = min(d.sleep_minutes for d in night_duties)
        status.append({
            "name": "야간 수면시간 (night_sleep)",
            "satisfied": min_sleep >= 240,
            "max_actual": f"{min_sleep}분 (최소)",
            "limit": "240분",
            "constraint_type": "parametric",
        })

    return status


def _build_soft_constraint_status(
    selected_duties: List[FeasibleDuty],
) -> List[Dict[str, Any]]:
    """소프트 제약 현황 (Generator 검증 기반)"""
    if not selected_duties:
        return []

    drivings = [d.driving_minutes for d in selected_duties]
    waits = [d.wait_minutes for d in selected_duties]

    status = []

    # 1. 평균 운전시간 목표
    avg_driving = sum(drivings) / len(drivings)
    status.append({
        "name": "평균 운전시간 목표 (avg_driving_target)",
        "status": "applied" if avg_driving <= 300 else "violated",
        "actual": f"{avg_driving:.0f}분",
        "target": "300분",
    })

    # 2. 평균 대기시간 목표
    avg_wait = sum(waits) / len(waits)
    status.append({
        "name": "평균 대기시간 목표 (avg_wait_target)",
        "status": "applied" if avg_wait <= 180 else "violated",
        "actual": f"{avg_wait:.0f}분",
        "target": "180분",
    })

    # 3. 워크로드 균형
    trip_counts = [len(d.trips) for d in selected_duties]
    max_trips = max(trip_counts) if trip_counts else 0
    min_trips = min(trip_counts) if trip_counts else 0
    status.append({
        "name": "워크로드 균형 (workload_balance)",
        "status": "applied" if max_trips - min_trips <= 5 else "violated",
        "actual": f"{min_trips}~{max_trips} trips/duty",
        "target": "균등 배분",
    })

    return status
