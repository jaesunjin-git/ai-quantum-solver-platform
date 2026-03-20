"""
domains/crew/result_converter.py ────────────────────────────
승무원 스케줄링 전용 결과 변환기.

Generic sp_result_converter의 convert_sp_result()를 확장하여
crew-specific KPI, constraint status, objective label 추가.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from engine.column_generator import FeasibleColumn, TaskItem
from engine.sp_result_converter import (
    convert_sp_result as _generic_convert,
    _build_kpi,
    _build_schedule_rows,
    _build_columns_detail,
    _save_result_files,
)

logger = logging.getLogger(__name__)


def convert_crew_result(
    solution: Dict[str, Any],
    column_map: Dict[int, FeasibleColumn],
    tasks: List[TaskItem],
    solver_id: str = "classical_cpu",
    solver_name: str = "CP-SAT (Set Partitioning)",
    project_dir: Optional[str] = None,
    objective_value: Optional[float] = None,
    # 하위 호환
    duty_map: Optional[Dict[int, FeasibleColumn]] = None,
    trips: Optional[List[TaskItem]] = None,
) -> Dict[str, Any]:
    """
    승무원 스케줄링 결과 변환.

    generic 변환 후 crew 전용 필드 추가:
      - objective_label: 승무원 수 최소화
      - constraint_status: Generator 검증 결과 기반 hard 제약 현황
      - soft_constraint_status: soft 제약 현황
      - KPI: day/night 분배, source 분포
    """
    _column_map = column_map or duty_map or {}
    _tasks = tasks or trips or []

    # ── generic 변환 먼저 실행 ──
    result = _generic_convert(
        solution=solution,
        column_map=_column_map,
        tasks=_tasks,
        solver_id=solver_id,
        solver_name=solver_name,
        project_dir=project_dir,
        objective_value=objective_value,
    )

    # ── crew 전용 보강 ──
    z_solution = solution.get("z", {})
    selected = [
        _column_map[int(cid)]
        for cid, val in z_solution.items()
        if isinstance(val, (int, float)) and val > 0 and int(cid) in _column_map
    ]
    selected.sort(key=lambda c: c.start_time)

    # objective label
    result["objective_label"] = "승무원 수 최소화 (Set Partitioning)"

    # crew KPI 보강
    kpi = result.get("kpi", {})
    day_columns = [c for c in selected if c.column_type not in ("night", "overnight")]
    night_columns = [c for c in selected if c.column_type in ("night", "overnight")]
    kpi["day_duties"] = len(day_columns)
    kpi["night_duties"] = len(night_columns)
    kpi["source_distribution"] = {}
    for c in selected:
        kpi["source_distribution"][c.source] = kpi["source_distribution"].get(c.source, 0) + 1
    result["kpi"] = kpi

    # schedule_summary 보강
    result["schedule_summary"] = {
        "total_duties": len(selected),
        "day_duties": len(day_columns),
        "night_duties": len(night_columns),
        "total_trips_covered": kpi.get("covered_trips", 0),
        "overlap_trips": kpi.get("overlap_trips", 0),
    }

    # constraint status (crew domain)
    result["constraint_status"] = _build_crew_constraint_status(selected, len(_tasks))
    result["soft_constraint_status"] = _build_crew_soft_status(selected)

    return result


# ── Crew 전용 Constraint Status ──────────────────────────────

def _build_crew_constraint_status(
    selected: List[FeasibleColumn],
    total_tasks: int,
) -> List[Dict[str, Any]]:
    """선택된 duty의 metrics 기반 hard 제약 달성 현황"""
    if not selected:
        return []

    actives = [c.active_minutes for c in selected]
    spans = [c.span_minutes for c in selected]
    idles = [c.idle_minutes for c in selected]
    night_cols = [c for c in selected if c.column_type in ("night", "overnight")]
    day_cols = [c for c in selected if c.column_type not in ("night", "overnight")]

    unique_tasks = set()
    for c in selected:
        unique_tasks.update(c.trips)

    status = []

    # 최대 운전시간
    status.append({
        "name": "최대 운전시간 (max_driving_time)",
        "satisfied": True,
        "max_actual": f"{max(actives)}분",
        "limit": "360분",
        "constraint_type": "parametric",
    })

    # 최대 근무시간
    works = [c.elapsed_minutes for c in selected]
    status.append({
        "name": "최대 근무시간 (max_work_time)",
        "satisfied": True,
        "max_actual": f"{max(works)}분",
        "limit": "660분",
        "constraint_type": "parametric",
    })

    # 최대 대기시간
    status.append({
        "name": "최대 대기시간 (max_wait_time)",
        "satisfied": True,
        "max_actual": f"{max(idles)}분",
        "limit": "300분",
        "constraint_type": "parametric",
    })

    # Trip 커버리지
    status.append({
        "name": "트립 커버리지 (trip_coverage)",
        "satisfied": len(unique_tasks) >= total_tasks,
        "max_actual": f"{len(unique_tasks)}/{total_tasks}",
        "limit": f"{total_tasks}",
        "constraint_type": "structural",
    })

    # 승무원 수
    status.append({
        "name": "총 승무원 수",
        "satisfied": True,
        "max_actual": f"{len(selected)}명",
        "limit": f"{len(selected)}명",
        "constraint_type": "structural",
    })

    # 주간/야간 분배
    status.append({
        "name": "주간 승무원",
        "satisfied": True,
        "max_actual": f"{len(day_cols)}명",
        "limit": f"{len(day_cols)}명",
        "constraint_type": "structural",
    })
    status.append({
        "name": "야간 승무원",
        "satisfied": True,
        "max_actual": f"{len(night_cols)}명",
        "limit": f"{len(night_cols)}명",
        "constraint_type": "structural",
    })

    # 야간 수면시간
    if night_cols:
        sleeps = [c.inactive_minutes for c in night_cols]
        min_sleep = min(sleeps) if sleeps else 0
        status.append({
            "name": "야간 수면시간 (night_sleep)",
            "satisfied": min_sleep >= 240,
            "max_actual": f"{min_sleep}분 (최소)",
            "limit": "240분",
            "constraint_type": "parametric",
        })

    return status


def _build_crew_soft_status(
    selected: List[FeasibleColumn],
) -> List[Dict[str, Any]]:
    """소프트 제약 현황"""
    if not selected:
        return []

    actives = [c.active_minutes for c in selected]
    idles = [c.idle_minutes for c in selected]
    trip_counts = [len(c.trips) for c in selected]
    n = len(selected)

    status = []

    avg_active = sum(actives) / n
    status.append({
        "name": "평균 운전시간 목표 (avg_driving_target)",
        "status": "applied" if avg_active <= 300 else "violated",
        "actual": f"{avg_active:.0f}분",
        "target": "300분",
    })

    avg_idle = sum(idles) / n
    status.append({
        "name": "평균 대기시간 목표 (avg_wait_target)",
        "status": "applied" if avg_idle <= 180 else "violated",
        "actual": f"{avg_idle:.0f}분",
        "target": "180분",
    })

    max_t = max(trip_counts) if trip_counts else 0
    min_t = min(trip_counts) if trip_counts else 0
    status.append({
        "name": "워크로드 균형 (workload_balance)",
        "status": "applied" if max_t - min_t <= 5 else "violated",
        "actual": f"{min_t}~{max_t} trips/duty",
        "target": "균등 배분",
    })

    return status
