"""
engine/result_interpreter.py
목적함수 유형별 솔버 결과 해석기

solution + model.json + trips.csv → 사용자에게 의미 있는 결과
"""
import json, os, logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

import pandas as pd

logger = logging.getLogger(__name__)


# ── 목적함수 유형 분류 ──
OBJECTIVE_TYPES = {
    "duty_count_min": ["sum(u[", "minimize.*u[", "min.*duty"],
    "cost_min":       ["cost", "비용", "expense"],
    "wait_min":       ["wait", "대기", "idle"],
    "time_min":       ["time", "시간", "duration"],
}

def classify_objective(expression: str) -> str:
    """목적함수 expression 문자열로 유형 분류"""
    expr_lower = (expression or "").lower()
    for obj_type, patterns in OBJECTIVE_TYPES.items():
        for pat in patterns:
            if pat.lower() in expr_lower:
                return obj_type
    return "generic"


def _parse_index_key(key: str) -> tuple:
    import re as _re
    nums = _re.findall(r'\d+', key)
    if not nums:
        return ()
    return tuple(int(n) for n in nums)

def _min_to_hhmm(minutes: float) -> str:
    """분 → HH:MM 변환"""
    if minutes is None:
        return "--:--"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


def interpret_result(
    solution: Dict[str, Any],
    math_model: Dict[str, Any],
    project_dir: str,
    solver_id: str = "",
    solver_name: str = "",
    status: str = "",
    objective_value: float = None,
    params: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    솔버 결과를 해석하여 사용자 친화적 구조 반환
    """
    # 목적함수 유형 판별
    obj_expr = math_model.get("objective", {}).get("expression", "")
    obj_type = classify_objective(obj_expr)

    OBJ_LABELS = {
        "duty_count_min": "듀티 수 최소화",
        "cost_min": "비용 최소화",
        "wait_min": "대기시간 최소화",
        "time_min": "운행시간 최소화",
        "generic": "목적함수 최적화",
    }

    # trips.csv 로드
    trips_path = os.path.join(project_dir, "normalized", "trips.csv")
    if not os.path.exists(trips_path):
        trips_path = os.path.join(project_dir, "phase1", "timetable_rows.csv")
    trips_df = pd.read_csv(trips_path)
    # 자정 넘김 보정: arr_time < dep_time이면 +1440
    _midnight = trips_df["trip_arr_time"] < trips_df["trip_dep_time"]
    if _midnight.any():
        trips_df.loc[_midnight, "trip_arr_time"] = trips_df.loc[_midnight, "trip_arr_time"] + 1440
        trips_df["trip_duration"] = trips_df["trip_arr_time"] - trips_df["trip_dep_time"]
        logger.info(f"Midnight correction: {_midnight.sum()} trips adjusted")

    # 파라미터 로드
    if params is None:
        params_path = os.path.join(project_dir, "normalized", "parameters.csv")
        if os.path.exists(params_path):
            pf = pd.read_csv(params_path)
            params = {}
            for _, row in pf.iterrows():
                try:
                    params[row.iloc[0]] = float(row.iloc[1])
                except:
                    params[row.iloc[0]] = row.iloc[1]
        else:
            params = {}

    prep_time = float(params.get("prep_time_minutes", 40))
    cleanup_time = float(params.get("cleanup_time_minutes", 30))
    max_driving = float(params.get("max_driving_minutes", 360))
    max_work = float(params.get("max_work_minutes", 660))
    max_stay = float(params.get("max_total_stay_minutes", 720))

    # ── solution 파싱 ──

    # y[j] = 활성 듀티 (승무원 j가 활성화)
    active_duties = set()
    for key, val in solution.get("y", {}).items():
        if val == 1.0:
            idx = _parse_index_key(key)
            if not idx: continue
            active_duties.add(idx[0])

    # x[i,j] = 트립→듀티 배정
    duty_trips = {}
    for key, val in solution.get("x", {}).items():
        if val == 1.0:
            idx = _parse_index_key(key)
            if len(idx) < 2: continue
            trip_id, duty_id = idx[0], idx[1]
            duty_trips.setdefault(duty_id, []).append(trip_id)

    # z[d], w[d] = 시작/종료 시각
    z_vals = {}
    for key, val in solution.get("z", {}).items():
        idx = _parse_index_key(key)
        if not idx: continue
        z_vals[idx[0]] = float(val)

    w_vals = {}
    for key, val in solution.get("w", {}).items():
        idx = _parse_index_key(key)
        if not idx: continue
        w_vals[idx[0]] = float(val)

    # 이 모델에서 duty_id = crew_id (y[j]가 곧 duty j)
    crew_assign = {}
    for duty_id in active_duties:
        crew_assign[duty_id] = duty_id

    # ── 듀티별 상세 생성 ──
    duties = []
    total_driving_all = 0
    total_idle_all = 0
    total_trips_covered = 0
    constraint_violations = []

    for d in sorted(active_duties):
        trip_ids = sorted(duty_trips.get(d, []))
        if not trip_ids:
            continue

        # trips.csv에서 해당 트립 정보 가져오기
        d_trips = trips_df[trips_df["trip_id"].isin(trip_ids)].copy()
        d_trips = d_trips.sort_values("trip_dep_time")

        trip_details = []
        total_driving = 0
        for _, row in d_trips.iterrows():
            trip_details.append({
                "trip_id": int(row["trip_id"]),
                "direction": row.get("direction", ""),
                "dep_station": row.get("dep_station", ""),
                "arr_station": row.get("arr_station", ""),
                "dep_time": float(row["trip_dep_time"]),
                "arr_time": float(row["trip_arr_time"]),
                "dep_hhmm": _min_to_hhmm(row["trip_dep_time"]),
                "arr_hhmm": _min_to_hhmm(row["trip_arr_time"]),
                "duration": float(row["trip_duration"]),
            })
            total_driving += float(row["trip_duration"])

        start_min_actual = float(d_trips["trip_dep_time"].min()) - prep_time
        start_min_solver = z_vals.get(d, start_min_actual)
        start_min = start_min_actual  # 실제 트립 기반 사용
        end_min_actual = float(d_trips["trip_arr_time"].max()) + cleanup_time
        end_min_solver = w_vals.get(d, end_min_actual)
        end_min = end_min_actual  # 실제 트립 기반 사용
        total_stay = end_min - start_min
        total_work = total_driving + prep_time + cleanup_time
        idle = total_stay - total_work

        # 제약 위반 체크
        violations = []
        if total_driving > max_driving:
            violations.append(f"운전시간 초과: {total_driving:.0f}/{max_driving:.0f}분")
        if total_work > max_work:
            violations.append(f"근무시간 초과: {total_work:.0f}/{max_work:.0f}분")
        if total_stay > max_stay:
            violations.append(f"체류시간 초과: {total_stay:.0f}/{max_stay:.0f}분")
        if violations:
            constraint_violations.extend([(d, v) for v in violations])

        duty_detail = {
            "duty_id": d,
            "crew_id": crew_assign.get(d),
            "trip_count": len(trip_ids),
            "trips": trip_details,
            "start_time_min": start_min,
            "end_time_min": end_min,
            "start_hhmm": _min_to_hhmm(start_min),
            "end_hhmm": _min_to_hhmm(end_min),
            "total_driving_min": round(total_driving, 1),
            "total_work_min": round(total_work, 1),
            "total_stay_min": round(total_stay, 1),
            "idle_min": round(max(idle, 0), 1),
            "violations": violations,
        }
        duties.append(duty_detail)

        total_driving_all += total_driving
        total_idle_all += max(idle, 0)
        total_trips_covered += len(trip_ids)

    # ── KPI 계산 ──
    total_trips = len(trips_df)
    n_duties = len(duties)

    kpi = {
        "active_duties": n_duties,
        "total_trips": total_trips,
        "covered_trips": total_trips_covered,
        "coverage_rate": round(total_trips_covered / total_trips * 100, 1) if total_trips > 0 else 0,
        "avg_trips_per_duty": round(total_trips_covered / n_duties, 1) if n_duties > 0 else 0,
        "total_driving_min": round(total_driving_all, 1),
        "total_idle_min": round(total_idle_all, 1),
        "avg_driving_per_duty": round(total_driving_all / n_duties, 1) if n_duties > 0 else 0,
        "avg_idle_per_duty": round(total_idle_all / n_duties, 1) if n_duties > 0 else 0,
        "driving_efficiency": round(total_driving_all / (total_driving_all + total_idle_all) * 100, 1) if (total_driving_all + total_idle_all) > 0 else 0,
        "constraint_violations": len(constraint_violations),
    }

    # 듀티 수 최소화인 경우 추가 KPI
    if obj_type == "duty_count_min":
        kpi["duty_reduction_vs_trips"] = round((1 - n_duties / total_trips) * 100, 1) if total_trips > 0 else 0
        kpi["earliest_start"] = _min_to_hhmm(min(d["start_time_min"] for d in duties)) if duties else "--:--"
        kpi["latest_end"] = _min_to_hhmm(max(d["end_time_min"] for d in duties)) if duties else "--:--"

    # 제약 충족 현황
    constraint_status = [
        {"name": "운전시간 상한", "limit": f"{max_driving:.0f}분",
         "max_actual": f"{max(d['total_driving_min'] for d in duties):.0f}분" if duties else "-",
         "satisfied": all(d["total_driving_min"] <= max_driving for d in duties)},
        {"name": "근무시간 상한", "limit": f"{max_work:.0f}분",
         "max_actual": f"{max(d['total_work_min'] for d in duties):.0f}분" if duties else "-",
         "satisfied": all(d["total_work_min"] <= max_work for d in duties)},
        {"name": "체류시간 상한", "limit": f"{max_stay:.0f}분",
         "max_actual": f"{max(d['total_stay_min'] for d in duties):.0f}분" if duties else "-",
         "satisfied": all(d["total_stay_min"] <= max_stay for d in duties)},
        {"name": "트립 커버리지", "limit": f"{total_trips}개",
         "max_actual": f"{total_trips_covered}개",
         "satisfied": total_trips_covered == total_trips},
    ]

    # 경고
    warnings = []
    if total_trips_covered < total_trips:
        warnings.append(f"미배정 트립 {total_trips - total_trips_covered}개 존재")
    if constraint_violations:
        warnings.append(f"제약 위반 {len(constraint_violations)}건 감지")

    result = {
        "objective_type": obj_type,
        "objective_label": OBJ_LABELS.get(obj_type, "최적화 결과"),
        "objective_value": objective_value,
        "solver_id": solver_id,
        "solver_name": solver_name,
        "status": status,
        "kpi": kpi,
        "duties": duties,
        "constraint_status": constraint_status,
        "warnings": warnings,
    }

    return result


def save_artifacts(
    project_dir: str,
    solution: Dict[str, Any],
    interpreted: Dict[str, Any],
    solver_id: str,
) -> Dict[str, str]:
    """
    산출물 파일 저장
    returns: {artifact_name: file_path}
    """
    results_dir = os.path.join(project_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    saved = {}

    # 1. 원시 solution
    sol_path = os.path.join(results_dir, f"solution_{solver_id}.json")
    with open(sol_path, "w", encoding="utf-8") as f:
        json.dump(solution, f, ensure_ascii=False, indent=2, default=str)
    saved["solution"] = sol_path

    # 2. 해석 결과 (JSON)
    interp_path = os.path.join(results_dir, f"interpretation_{solver_id}.json")
    with open(interp_path, "w", encoding="utf-8") as f:
        json.dump(interpreted, f, ensure_ascii=False, indent=2, default=str)
    saved["interpretation"] = interp_path

    # 3. 듀티 배정표 CSV
    if interpreted.get("duties"):
        rows = []
        for duty in interpreted["duties"]:
            for trip in duty.get("trips", []):
                rows.append({
                    "duty_id": duty["duty_id"],
                    "crew_id": duty.get("crew_id", ""),
                    "duty_start": duty["start_hhmm"],
                    "duty_end": duty["end_hhmm"],
                    "trip_id": trip["trip_id"],
                    "direction": trip["direction"],
                    "dep_station": trip["dep_station"],
                    "arr_station": trip["arr_station"],
                    "dep_time": trip["dep_hhmm"],
                    "arr_time": trip["arr_hhmm"],
                    "duration_min": trip["duration"],
                })
        schedule_path = os.path.join(results_dir, f"duty_schedule_{solver_id}.csv")
        pd.DataFrame(rows).to_csv(schedule_path, index=False, encoding="utf-8-sig")
        saved["duty_schedule"] = schedule_path

    # 4. KPI 요약
    kpi_path = os.path.join(results_dir, f"kpi_{solver_id}.json")
    with open(kpi_path, "w", encoding="utf-8") as f:
        json.dump(interpreted.get("kpi", {}), f, ensure_ascii=False, indent=2)
    saved["kpi"] = kpi_path

    logger.info(f"Artifacts saved: {list(saved.keys())} → {results_dir}")
    return saved
