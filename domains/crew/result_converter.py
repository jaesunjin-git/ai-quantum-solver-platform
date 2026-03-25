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
from engine.sp_result_converter import convert_sp_result as _generic_convert

logger = logging.getLogger(__name__)

# YAML objective_display 캐시
_objective_display_cache: Optional[Dict] = None


def _load_objective_display() -> Dict:
    """result_mapping.yaml에서 objective_display 섹션 로딩 (캐시)"""
    global _objective_display_cache
    if _objective_display_cache is not None:
        return _objective_display_cache

    import os
    import yaml
    yaml_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "knowledge", "domains", "railway", "result_mapping.yaml"
    )
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _objective_display_cache = data.get("objective_display", {})
    except Exception as e:
        logger.warning(f"Failed to load objective_display: {e}")
        _objective_display_cache = {}
    return _objective_display_cache


def _resolve_objective_display(
    objective_type: str, kpi: Dict, objective_value: Optional[float]
) -> Dict[str, str]:
    """YAML 기반 목적함수 표시 정보 해석"""
    display_config = _load_objective_display()
    cfg = display_config.get(objective_type, {})

    label = cfg.get("label_ko", "목적함수 최적화")
    direction = cfg.get("direction", "")
    suffix = cfg.get("value_suffix", "")

    # value_source 해석 (dotted key: "kpi.active_duties" → kpi["active_duties"])
    value_source = cfg.get("value_source", "objective_value")
    if value_source == "objective_value":
        raw = objective_value
    elif value_source.startswith("kpi."):
        kpi_key = value_source.split(".", 1)[1]
        raw = kpi.get(kpi_key)
    else:
        raw = objective_value

    if raw is not None:
        # 정수면 정수 표시, 소수면 소수 표시
        if isinstance(raw, float) and raw == int(raw):
            display_value = f"{int(raw)}{suffix}"
        else:
            display_value = f"{raw}{suffix}"
    else:
        display_value = ""

    return {"label": label, "display_value": display_value, "direction": direction}


def convert_crew_result(
    solution: Dict[str, Any],
    column_map: Dict[int, FeasibleColumn],
    tasks: List[TaskItem],
    solver_id: str = "classical_cpu",
    solver_name: str = "CP-SAT (Set Partitioning)",
    project_dir: Optional[str] = None,
    objective_value: Optional[float] = None,
    params: Optional[Dict] = None,
    objective_type: str = "minimize_duties",
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

    # objective display (YAML 기반)
    obj_display = _resolve_objective_display(objective_type, result.get("kpi", {}), objective_value)
    result["objective_label"] = obj_display["label"]
    result["objective_display_value"] = obj_display["display_value"]
    result["objective_direction"] = obj_display["direction"]
    result["objective_type"] = objective_type

    # crew KPI 보강
    kpi = result.get("kpi", {})
    from engine.compiler.sp_problem import ColumnType
    day_columns = [c for c in selected if c.column_type in ColumnType.DAY_GROUP]
    night_columns = [c for c in selected if c.column_type in ColumnType.NIGHT_GROUP]
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

    # constraint status (crew domain) — params 기반 실제 검증
    result["constraint_status"] = _build_crew_constraint_status(selected, len(_tasks), params=params)
    result["soft_constraint_status"] = _build_crew_soft_status(selected)

    return result


# ── Crew 전용 Constraint Status ──────────────────────────────

def _build_crew_constraint_status(
    selected: List[FeasibleColumn],
    total_tasks: int,
    params: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """선택된 duty의 metrics 기반 hard 제약 달성 현황 (실제 limit 기반 검증)"""
    if not selected:
        return []

    params = params or {}

    # 제약 기준값: params에서 로딩 (하드코딩 제거)
    max_driving_limit = int(params.get('max_driving_minutes', 360))
    max_work_limit = int(params.get('max_work_minutes', 660))
    max_wait_limit = int(params.get('max_wait_minutes', 300))
    min_sleep_limit = int(params.get('min_night_sleep_minutes', 240))
    total_duties_param = params.get('total_duties')
    day_crew_param = params.get('day_crew_count')
    night_crew_param = params.get('night_crew_count')

    actives = [c.active_minutes for c in selected]
    idles = [c.idle_minutes for c in selected]
    works = [c.elapsed_minutes for c in selected]
    from engine.compiler.sp_problem import ColumnType
    night_cols = [c for c in selected if c.column_type in ColumnType.NIGHT_GROUP]
    day_cols = [c for c in selected if c.column_type in ColumnType.DAY_GROUP]

    unique_tasks = set()
    for c in selected:
        unique_tasks.update(c.trips)

    status = []

    # 최대 운전시간 — 실제 검증
    max_active = max(actives)
    driving_violations = sum(1 for a in actives if a > max_driving_limit)
    status.append({
        "name": "최대 운전시간 (max_driving_time)",
        "satisfied": driving_violations == 0,
        "max_actual": f"{max_active}분",
        "limit": f"{max_driving_limit}분",
        "constraint_type": "parametric",
        "violation_count": driving_violations,
    })

    # 최대 근무시간 — 실제 검증
    max_work = max(works)
    work_violations = sum(1 for w in works if w > max_work_limit)
    status.append({
        "name": "최대 근무시간 (max_work_time)",
        "satisfied": work_violations == 0,
        "max_actual": f"{max_work}분",
        "limit": f"{max_work_limit}분",
        "constraint_type": "parametric",
        "violation_count": work_violations,
    })

    # 최대 대기시간 — 실제 검증
    max_idle = max(idles)
    idle_violations = sum(1 for i in idles if i > max_wait_limit)
    status.append({
        "name": "최대 대기시간 (max_wait_time)",
        "satisfied": idle_violations == 0,
        "max_actual": f"{max_idle}분",
        "limit": f"{max_wait_limit}분",
        "constraint_type": "parametric",
        "violation_count": idle_violations,
    })

    # Trip 커버리지
    status.append({
        "name": "트립 커버리지 (trip_coverage)",
        "satisfied": len(unique_tasks) >= total_tasks,
        "max_actual": f"{len(unique_tasks)}/{total_tasks}",
        "limit": f"{total_tasks}",
        "constraint_type": "structural",
    })

    # 총 승무원 수 — params 기준 검증
    if total_duties_param is not None:
        total_limit = int(total_duties_param)
        status.append({
            "name": "총 승무원 수",
            "satisfied": len(selected) == total_limit,
            "max_actual": f"{len(selected)}명",
            "limit": f"{total_limit}명",
            "constraint_type": "structural",
        })

    # 주간 승무원 — params 기준 검증
    if day_crew_param is not None:
        day_limit = int(day_crew_param)
        status.append({
            "name": "주간 승무원",
            "satisfied": len(day_cols) == day_limit,
            "max_actual": f"{len(day_cols)}명",
            "limit": f"{day_limit}명",
            "constraint_type": "structural",
        })

    # 야간 승무원 — params 기준 검증
    if night_crew_param is not None:
        night_limit = int(night_crew_param)
        status.append({
            "name": "야간 승무원",
            "satisfied": len(night_cols) == night_limit,
            "max_actual": f"{len(night_cols)}명",
            "limit": f"{night_limit}명",
            "constraint_type": "structural",
        })

    # 야간 수면시간 — overnight column만 대상 (morning_only는 수면 없음)
    overnight_cols = [c for c in night_cols if c.column_type in ("overnight",)]
    if overnight_cols:
        sleeps = [c.inactive_minutes for c in overnight_cols]
        min_sleep = min(sleeps) if sleeps else 0
        sleep_violations = sum(1 for s in sleeps if s < min_sleep_limit)
        status.append({
            "name": "야간 수면시간 (night_sleep)",
            "satisfied": sleep_violations == 0,
            "max_actual": f"{min_sleep}분 (최소)",
            "limit": f"{min_sleep_limit}분",
            "constraint_type": "parametric",
            "violation_count": sleep_violations,
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
