"""
sp_result_converter.py ──────────────────────────────────────
Set Partitioning 결과 변환 (도메인 무관 Base).

SP solver 해를 프론트엔드 포맷으로 변환하는 기본 인터페이스.
도메인별 변환기는 이 모듈의 convert_sp_result()를 override하거나,
별도의 converter를 solver_pipeline에 주입.

GR-1: 이 모듈에 도메인 특화 로직 없음.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Protocol

from engine.column_generator import FeasibleColumn, TaskItem

logger = logging.getLogger(__name__)


# ── Converter Protocol (도메인 주입용) ────────────────────────

class ResultConverterProtocol(Protocol):
    """SP 결과 변환기 프로토콜. 도메인별 converter가 구현."""

    def convert(
        self,
        solution: Dict[str, Any],
        column_map: Dict[int, FeasibleColumn],
        tasks: List[TaskItem],
        solver_id: str,
        solver_name: str,
        project_dir: Optional[str],
        objective_value: Optional[float],
    ) -> Dict[str, Any]:
        """SP 결과 → interpretation dict 변환"""
        ...


# ── Generic 변환 (도메인 무관) ────────────────────────────────

def convert_sp_result(
    solution: Dict[str, Any],
    column_map: Optional[Dict[int, FeasibleColumn]] = None,
    tasks: Optional[List[TaskItem]] = None,
    solver_id: str = "classical_cpu",
    solver_name: str = "CP-SAT (Set Partitioning)",
    project_dir: Optional[str] = None,
    objective_value: Optional[float] = None,
    params: Optional[Dict] = None,
    objective_type: str = "minimize_duties",
    best_bound: Optional[float] = None,
    extra_constraints: Optional[List] = None,
    # 하위 호환
    duty_map: Optional[Dict[int, FeasibleColumn]] = None,
    trips: Optional[List[TaskItem]] = None,
) -> Dict[str, Any]:
    """
    SP 결과를 generic 포맷으로 변환 (도메인 무관 기본 구현).

    도메인별 converter가 override하지 않으면 이 함수가 사용됨.
    프론트엔드 호환 포맷 (schedule, kpi, interpretation) 생성.
    """
    # 하위 호환: duty_map/trips 인자명
    _column_map = column_map or duty_map or {}
    _tasks = tasks or trips or []

    task_map = {t.id: t for t in _tasks}

    # ── 1. 선택된 column 추출 ──
    z_solution = solution.get("z", {})
    selected_ids = [
        int(cid) for cid, val in z_solution.items()
        if isinstance(val, (int, float)) and val > 0
    ]

    selected = []
    for cid in selected_ids:
        c = _column_map.get(cid)
        if c:
            selected.append(c)

    selected.sort(key=lambda c: c.start_time)
    logger.info(f"SP result: {len(selected)} columns selected")

    # ── 2. Schedule rows ──
    schedule_rows = _build_schedule_rows(selected, task_map)

    # ── 3. KPI ──
    kpi = _build_kpi(selected, _tasks)

    # ── 4. Column 상세 (interpretation용) ──
    columns_detail = _build_columns_detail(selected, task_map)

    # ── 5. Side Constraint 결과 ──
    side_constraint_status = _build_side_constraint_status(
        selected, extra_constraints or []
    )

    # ── 6. Optimality Gap ──
    gap_info = None
    if best_bound is not None and objective_value is not None and objective_value > 0:
        gap_pct = abs(objective_value - best_bound) / objective_value * 100
        gap_info = {
            "best_bound": round(best_bound, 2),
            "objective_value": round(objective_value, 2),
            "gap_percent": round(gap_pct, 2),
            "is_optimal": gap_pct < 0.01,
        }

    # ── 7. Interpretation dict ──
    interpretation = {
        "objective_type": "minimize",
        "objective_label": "Column 수 최소화 (Set Partitioning)",
        "objective_value": objective_value,
        "solver_id": solver_id,
        "solver_name": solver_name,
        "status": "OPTIMAL" if (gap_info and gap_info["is_optimal"]) else "FEASIBLE",
        "model_type": "SetPartitioning",
        "kpi": kpi,
        "duties": columns_detail,
        "schedule_summary": kpi,
        "constraint_status": [],
        "soft_constraint_status": side_constraint_status,
        "optimality_gap": gap_info,
        "warnings": [],
    }

    # ── 6. 파일 저장 ──
    if project_dir:
        _save_result_files(
            project_dir, solver_id, schedule_rows, kpi,
            interpretation, selected_ids
        )

    return interpretation


# ── Generic schedule rows 생성 ────────────────────────────────

def _build_schedule_rows(
    selected: List[FeasibleColumn],
    task_map: Dict[int, TaskItem],
) -> List[Dict[str, Any]]:
    """선택된 column → schedule row 목록"""
    rows = []
    for idx, col in enumerate(selected, 1):
        ds_hh = f"{col.start_time // 60:02d}:{col.start_time % 60:02d}"
        de_hh = f"{col.end_time // 60:02d}:{col.end_time % 60:02d}"

        for tid in col.trips:
            task = task_map.get(tid)
            if task is None:
                continue

            dep_hh = f"{task.dep_time // 60:02d}:{task.dep_time % 60:02d}"
            arr_hh = f"{task.arr_time // 60:02d}:{task.arr_time % 60:02d}"

            rows.append({
                "duty_id": idx,
                "crew_id": idx,
                "duty_type": col.column_type,
                "duty_start": ds_hh,
                "duty_end": de_hh,
                "trip_id": tid,
                "direction": task.direction,
                "dep_station": task.dep_station,
                "arr_station": task.arr_station,
                "dep_time": dep_hh,
                "arr_time": arr_hh,
                "duration_min": task.duration,
            })

    rows.sort(key=lambda r: (r["duty_id"], r["dep_time"]))
    return rows


# ── Generic KPI 생성 ─────────────────────────────────────────

def _build_kpi(
    selected: List[FeasibleColumn],
    all_tasks: List[TaskItem],
) -> Dict[str, Any]:
    """Generic KPI 계산"""
    total_assigned = sum(len(c.trips) for c in selected)
    unique_tasks = set()
    for c in selected:
        unique_tasks.update(c.trips)
    overlap = total_assigned - len(unique_tasks)

    total_active = sum(c.active_minutes for c in selected)
    total_idle = sum(c.idle_minutes for c in selected)
    total_span = sum(c.span_minutes for c in selected)
    n = max(len(selected), 1)

    # 균형 지표: duty당 trip 수 분포
    trip_counts = [len(c.trips) for c in selected]
    avg_trips = total_assigned / n
    trip_std_dev = (sum((tc - avg_trips) ** 2 for tc in trip_counts) / n) ** 0.5 if n > 1 else 0.0
    max_min_trip_gap = max(trip_counts) - min(trip_counts) if trip_counts else 0

    # 운행 시간대 (earliest start ~ latest end)
    earliest = min((c.start_time for c in selected), default=0)
    latest = max((c.end_time for c in selected), default=0)
    earliest_hhmm = f"{earliest // 60:02d}:{earliest % 60:02d}"
    latest_hhmm = f"{latest // 60:02d}:{latest % 60:02d}"

    return {
        "active_duties": len(selected),
        "total_trips": len(all_tasks),
        "covered_trips": len(unique_tasks),
        "coverage_rate": round(len(unique_tasks) / max(len(all_tasks), 1) * 100, 1),
        "overlap_trips": overlap,
        "avg_trips_per_duty": round(avg_trips, 1),
        "trip_std_dev": round(trip_std_dev, 2),
        "max_min_trip_gap": max_min_trip_gap,
        "total_driving_min": total_active,
        "total_idle_min": total_idle,
        "total_span_min": total_span,
        "avg_driving_per_duty": round(total_active / n, 1),
        "avg_idle_per_duty": round(total_idle / n, 1),
        "driving_efficiency": round(total_active / max(total_span, 1) * 100, 1),
        "constraint_violations": 0,
        "earliest_start": earliest,
        "earliest_start_hhmm": earliest_hhmm,
        "latest_end": latest,
        "latest_end_hhmm": latest_hhmm,
    }


# ── Generic column 상세 ──────────────────────────────────────

def _build_columns_detail(
    selected: List[FeasibleColumn],
    task_map: Dict[int, TaskItem],
) -> List[Dict[str, Any]]:
    """Column 상세 정보 (interpretation용)"""
    details = []
    for idx, col in enumerate(selected, 1):
        trips_detail = []
        for tid in col.trips:
            task = task_map.get(tid)
            if task:
                trips_detail.append({
                    "trip_id": tid,
                    "dep_hhmm": f"{task.dep_time // 60:02d}:{task.dep_time % 60:02d}",
                    "arr_hhmm": f"{task.arr_time // 60:02d}:{task.arr_time % 60:02d}",
                    "dep_time": task.dep_time,      # 분 단위 숫자 (타임라인용)
                    "arr_time": task.arr_time,       # 분 단위 숫자
                    "dep_station": task.dep_station,
                    "arr_station": task.arr_station,
                    "duration": task.duration,
                    "direction": task.direction,
                })

        ds_hh = f"{col.start_time // 60:02d}:{col.start_time % 60:02d}"
        de_hh = f"{col.end_time // 60:02d}:{col.end_time % 60:02d}"

        details.append({
            "duty_id": idx,
            "crew_id": idx,
            "is_night": col.column_type in ("night", "overnight"),
            "column_type": col.column_type,
            "start_time": col.start_time,
            "end_time": col.end_time,
            "start_hhmm": ds_hh,
            "end_hhmm": de_hh,
            "trip_count": len(col.trips),
            "total_driving_min": col.active_minutes,
            "idle_min": col.idle_minutes,
            "total_stay_min": col.span_minutes,
            "total_work_min": col.elapsed_minutes,
            "start_time_min": col.start_time,
            "trips": trips_detail,
            "driving_minutes": col.active_minutes,
            "wait_minutes": col.idle_minutes,
            "span_minutes": col.span_minutes,
            "break_minutes": col.pause_minutes,
            "sleep_minutes": col.inactive_minutes,
            "source": col.source,
            "cost": round(col.cost, 2),
            "violations": [],
        })

    return details


# ── Side Constraint 결과 표시 ────────────────────────────────

def _build_side_constraint_status(
    selected: List[FeasibleColumn],
    extra_constraints: List,
) -> List[Dict]:
    """Side Constraint (aggregate_avg, cardinality 등)의 충족 상태를 계산."""
    if not extra_constraints or not selected:
        return []

    results = []
    selected_ids = {c.id for c in selected}

    for con in extra_constraints:
        # SPConstraint 객체의 필드 확인
        name = getattr(con, "name", "")
        constraint_ref = getattr(con, "constraint_ref", "")
        is_soft = getattr(con, "is_soft", False)
        coefficients = getattr(con, "coefficients", None)
        operator = getattr(con, "operator", "<=")
        rhs = getattr(con, "rhs", 0)
        column_ids = getattr(con, "column_ids", [])

        # 선택된 column 기준으로 실제 값 계산
        if coefficients:
            # aggregate_avg/sum: Σ coeff[k] * z[k]
            actual = sum(
                coefficients.get(cid, 0) for cid in selected_ids
                if cid in coefficients
            )
            # aggregate_avg의 경우 rhs=0이고 coeff=field-target
            # 실제 위반량 = actual (>0이면 위반)
            satisfied = _check_op(actual, operator, rhs)

            # 사용자 친화적 표시: 평균값 역산
            if "aggregate_avg" in name:
                # 선택된 column 수
                n_selected = len(selected)
                # coeff = field_value - target → actual = Σ(field-target) = Σfield - n*target
                # → avg_field = (actual + n*target) / n ... 이건 원래 target을 모름
                # 단순히 satisfied/violated + actual 표시
                entry = {
                    "name": name,
                    "constraint_ref": constraint_ref,
                    "type": "aggregate_avg",
                    "is_soft": is_soft,
                    "satisfied": satisfied,
                    "violation_amount": round(actual, 1) if not satisfied else 0,
                    "description": f"{'충족' if satisfied else '위반'} (편차합={actual:.1f})",
                }
            else:
                entry = {
                    "name": name,
                    "constraint_ref": constraint_ref,
                    "type": "aggregate",
                    "is_soft": is_soft,
                    "satisfied": satisfied,
                    "actual": round(actual, 1),
                    "rhs": rhs,
                    "operator": operator,
                }
        else:
            # cardinality: Σ z[k] (eligible)
            actual = sum(1 for cid in column_ids if cid in selected_ids)
            satisfied = _check_op(actual, operator, rhs)

            entry = {
                "name": name,
                "constraint_ref": constraint_ref,
                "type": "cardinality",
                "is_soft": is_soft,
                "satisfied": satisfied,
                "actual": actual,
                "required": rhs,
                "operator": operator,
                "description": f"{'충족' if satisfied else '위반'} ({actual}/{rhs})",
            }

        results.append(entry)

    return results


def _check_op(lhs: float, operator: str, rhs: float) -> bool:
    """연산자 기반 충족 여부 판정."""
    if operator == "<=":
        return lhs <= rhs + 0.001  # float 오차 허용
    elif operator == ">=":
        return lhs >= rhs - 0.001
    elif operator == "==":
        return abs(lhs - rhs) < 0.001
    return True


# ── 파일 저장 ────────────────────────────────────────────────

def _save_result_files(
    project_dir: str,
    solver_id: str,
    schedule_rows: List[Dict],
    kpi: Dict,
    interpretation: Dict,
    selected_ids: List[int],
):
    """결과 파일 저장 (CSV, JSON)"""
    results_dir = os.path.join(project_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    # schedule CSV
    csv_path = os.path.join(results_dir, f"duty_schedule_{solver_id}.csv")
    fieldnames = [
        "duty_id", "crew_id", "duty_type", "duty_start", "duty_end",
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
            "selected_column_ids": selected_ids,
            "total_columns": len(selected_ids),
            "solver_id": solver_id,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"SP results saved to {results_dir}")
