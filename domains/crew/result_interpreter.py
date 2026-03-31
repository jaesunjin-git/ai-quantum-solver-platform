"""
domains/crew/result_interpreter.py — v2.1
철도 승무 도메인 결과 해석기.

RailwayResultInterpreter — GenericResultInterpreter를 상속하여
crew scheduling 전용 결과 해석(듀티 배정표, KPI, 제약 현황) 제공.

이 파일은 crew scheduling problem type의 코드이므로 domains/crew/ 에 위치.
플랫폼 Base(GenericResultInterpreter)는 engine/result_interpreter_base.py 에 위치.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from engine.result_interpreter_base import (
    GenericResultInterpreter,
    get_interpreter,
    register_interpreter,
    _parse_index_key,
    _min_to_hhmm,
)

logger = logging.getLogger(__name__)


# ── Backward-compat: keep module-level classify_objective ──

def classify_objective(expression: str) -> str:
    """목적함수 expression 문자열로 유형 분류 (하위 호환용)"""
    interp = get_interpreter("railway")
    obj_type, _ = interp.classify_objective(expression)
    return obj_type


# ── Railway-specific helpers ──

def _max_inter_trip_gap(trips: list) -> float:
    """듀티 내 트립 간 최대 공백(분) 계산"""
    if len(trips) < 2:
        return 0.0
    sorted_trips = sorted(trips, key=lambda t: t["dep_time"])
    max_gap = 0.0
    for i in range(1, len(sorted_trips)):
        gap = sorted_trips[i]["dep_time"] - sorted_trips[i - 1]["arr_time"]
        if gap > max_gap:
            max_gap = gap
    return max_gap


class RailwayResultInterpreter(GenericResultInterpreter):
    """
    Railway crew-scheduling result interpreter.

    Reads constraint labels and objective patterns from
    knowledge/domains/railway/result_mapping.yaml, but keeps the
    domain-specific solution parsing and KPI logic in code.
    """

    def __init__(self, domain: str = "railway"):
        super().__init__(domain)

    # ── Hard constraint post-solve check ──

    def _check_hard_constraint(
        self,
        cname: str,
        duties: list,
        params: dict,
        total_trips: int,
        covered_trips: int,
    ) -> Optional[dict]:
        label = self.get_hard_label(cname)

        # Structural constraints
        if cname == "trip_coverage":
            return {
                "name": label, "limit": f"{total_trips}개",
                "max_actual": f"{covered_trips}개",
                "satisfied": covered_trips == total_trips,
                "constraint_type": "structural",
            }

        if cname == "no_overlap":
            all_tids = [t["trip_id"] for d in duties for t in d.get("trips", [])]
            overlap_cnt = len(all_tids) - len(set(all_tids))
            return {
                "name": label, "limit": "중복 없음",
                "max_actual": "없음" if overlap_cnt == 0 else f"{overlap_cnt}건",
                "satisfied": overlap_cnt == 0,
                "constraint_type": "structural",
            }

        if cname == "crew_activation_linking":
            empty = sum(1 for d in duties if d["trip_count"] == 0)
            return {
                "name": label, "limit": "빈 듀티 없음",
                "max_actual": "없음" if empty == 0 else f"빈 듀티 {empty}개",
                "satisfied": empty == 0,
                "constraint_type": "structural",
            }

        if cname == "preparation_time":
            prep = self.get_param(params, "prep_time_minutes")
            return {
                "name": label, "limit": f"{prep:.0f}분",
                "max_actual": "적용됨", "satisfied": True,
                "constraint_type": "structural",
            }

        if cname == "cleanup_time":
            cleanup = self.get_param(params, "cleanup_time_minutes")
            return {
                "name": label, "limit": f"{cleanup:.0f}분",
                "max_actual": "적용됨", "satisfied": True,
                "constraint_type": "structural",
            }

        # Parametric constraints
        if cname == "max_driving_time":
            limit = self.get_param(params, "max_driving_minutes")
            max_val = max((d["total_driving_min"] for d in duties), default=0)
            return {
                "name": label, "limit": f"{limit:.0f}분",
                "max_actual": f"{max_val:.0f}분",
                "satisfied": max_val <= limit,
                "constraint_type": "parametric",
            }

        if cname == "max_work_time":
            limit = self.get_param(params, "max_work_minutes")
            max_val = max((d["total_work_min"] for d in duties), default=0)
            return {
                "name": label, "limit": f"{limit:.0f}분",
                "max_actual": f"{max_val:.0f}분",
                "satisfied": max_val <= limit,
                "constraint_type": "parametric",
            }

        if cname == "max_wait_time":
            limit = float(params.get("max_wait_minutes", params.get("max_idle_minutes", 0)))
            if limit <= 0:
                return {
                    "name": label, "limit": "미설정",
                    "max_actual": "-", "satisfied": True,
                    "constraint_type": "parametric",
                }
            max_val = max((d["idle_min"] for d in duties), default=0)
            return {
                "name": label, "limit": f"{limit:.0f}분",
                "max_actual": f"{max_val:.0f}분",
                "satisfied": max_val <= limit,
                "constraint_type": "parametric",
            }

        if cname == "mandatory_break":
            limit = float(params.get("mandatory_break_minutes", 0))
            if limit <= 0:
                return {
                    "name": label, "limit": "미설정",
                    "max_actual": "-", "satisfied": True,
                    "constraint_type": "parametric",
                }
            violated = sum(
                1 for d in duties if _max_inter_trip_gap(d.get("trips", [])) < limit
            )
            return {
                "name": label, "limit": f"{limit:.0f}분",
                "max_actual": "충족" if violated == 0 else f"위반 {violated}건",
                "satisfied": violated == 0,
                "constraint_type": "parametric",
            }

        if cname == "meal_break_guarantee":
            limit = float(params.get("meal_break_minutes", 0))
            if limit <= 0:
                return {
                    "name": label, "limit": "미설정",
                    "max_actual": "-", "satisfied": True,
                    "constraint_type": "parametric",
                }
            violated = sum(
                1 for d in duties if _max_inter_trip_gap(d.get("trips", [])) < limit
            )
            return {
                "name": label, "limit": f"{limit:.0f}분",
                "max_actual": "충족" if violated == 0 else f"위반 {violated}건",
                "satisfied": violated == 0,
                "constraint_type": "parametric",
            }

        # Unknown hard constraint → structural applied
        return {
            "name": label, "limit": "적용됨",
            "max_actual": "적용됨", "satisfied": True,
            "constraint_type": "structural",
        }

    def _build_soft_constraint_status(self, soft_constraints: list) -> list:
        return [
            {
                "name": self.get_soft_label(
                    c.get("name", c.get("id", ""))
                ),
                "id": c.get("name", c.get("id", "")),
                "status": "skipped",
                "note": "현재 최적화에 미반영 (소프트 제약)",
            }
            for c in soft_constraints
        ]

    # ── Main interpretation ──

    def interpret(
        self,
        solution: Dict[str, Any],
        math_model: Dict[str, Any],
        project_dir: str,
        solver_id: str = "",
        solver_name: str = "",
        status: str = "",
        objective_value: float = None,
        params: Dict[str, Any] = None,
        policy_snapshot: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        # Objective
        obj_expr = math_model.get("objective", {}).get("expression", "")
        obj_type, obj_label = self.classify_objective(obj_expr)

        # Load trips data
        trips_df = self.load_entity_data(project_dir)

        # Midnight correction — policy-aware inverse display (snapshot 재사용)
        try:
            from engine.policy import PolicyEngine, PolicyResolutionContext, ResolvedPolicies, TimeAxisPolicy, OvernightPolicy
            _domain = math_model.get("domain", "railway")
            _pe = PolicyEngine(_domain)
            if _pe.has_policies():
                # snapshot이 있으면 재사용 (single resolve 원칙)
                if policy_snapshot and policy_snapshot.get("resolved"):
                    _rd = policy_snapshot["resolved"]
                    _ta = _rd.get("time_axis", {})
                    _on = _rd.get("overnight", {})
                    _resolved = ResolvedPolicies(
                        time_axis=TimeAxisPolicy(
                            period_minutes=_ta.get("period_minutes", 1440),
                            service_day_anchor_minute=_ta.get("service_day_anchor_minute", 0),
                            horizon_days=_ta.get("horizon_days", 1),
                            timezone=_ta.get("timezone", "UTC"),
                            shift_policy=_ta.get("shift_policy", "shift_if_before_anchor"),
                        ),
                        overnight=OvernightPolicy(
                            active=_on.get("active", False),
                            min_sleep_minutes=_on.get("min_sleep_minutes", 240),
                            sleep_window_start=_on.get("sleep_window_start", 0),
                            sleep_window_end=_on.get("sleep_window_end", 360),
                            sleep_counts_as_work=_on.get("sleep_counts_as_work", False),
                        ),
                        resolved_hash=_rd.get("resolved_hash", ""),
                    )
                    logger.info(f"Midnight correction: using policy snapshot (hash={_resolved.resolved_hash})")
                else:
                    _ctx = PolicyResolutionContext(domain=_domain, clarification_params=params or {})
                    _resolved = _pe.resolve(_ctx)

                for _col in ["trip_dep_time", "trip_arr_time"]:
                    _abs_col = _col.replace("_time", "_abs_minute")
                    for _c in [_abs_col, _col]:
                        if _c in trips_df.columns:
                            trips_df[_col] = trips_df[_c].apply(
                                lambda v: _pe.inverse_display(_abs_col, float(v), _resolved)[0]
                                if v is not None else v
                            )
                            break
                logger.info("Midnight correction: policy-aware inverse applied")
            else:
                # Fallback: legacy hardcoded correction
                _midnight = trips_df["trip_arr_time"] < trips_df["trip_dep_time"]
                if _midnight.any():
                    trips_df.loc[_midnight, "trip_arr_time"] += 1440
                    trips_df["trip_duration"] = trips_df["trip_arr_time"] - trips_df["trip_dep_time"]
                    logger.info(f"Midnight correction (legacy): {_midnight.sum()} trips adjusted")
        except Exception as _e:
            # Fallback: legacy hardcoded correction
            _midnight = trips_df["trip_arr_time"] < trips_df["trip_dep_time"]
            if _midnight.any():
                trips_df.loc[_midnight, "trip_arr_time"] += 1440
                trips_df["trip_duration"] = trips_df["trip_arr_time"] - trips_df["trip_dep_time"]
                logger.info(f"Midnight correction (legacy fallback): {_midnight.sum()} trips adjusted")

        # Load parameters
        if params is None:
            params = self.load_parameters(project_dir)

        prep_time = self.get_param(params, "prep_time_minutes")
        cleanup_time = self.get_param(params, "cleanup_time_minutes")
        max_driving = self.get_param(params, "max_driving_minutes")
        max_work = self.get_param(params, "max_work_minutes")
        max_stay = self.get_param(params, "max_total_stay_minutes")

        # ── Parse solution variables (YAML-driven keys) ──
        act_key = self.get_var_key("activation")    # "y"
        assign_key = self.get_var_key("assignment")  # "x"
        start_key = self.get_var_key("start_time")   # "z"
        end_key = self.get_var_key("end_time")        # "w"

        # y[j] = active duties
        active_duties = set()
        for key, val in solution.get(act_key, {}).items():
            if val == 1.0:
                idx = _parse_index_key(key)
                if idx:
                    active_duties.add(idx[0])

        # x[i,j] = trip→duty assignment
        duty_trips = {}
        for key, val in solution.get(assign_key, {}).items():
            if val == 1.0:
                idx = _parse_index_key(key)
                if len(idx) >= 2:
                    duty_trips.setdefault(idx[1], []).append(idx[0])

        # z[d], w[d] = start/end times
        z_vals = {}
        for key, val in solution.get(start_key, {}).items():
            idx = _parse_index_key(key)
            if idx:
                z_vals[idx[0]] = float(val)

        w_vals = {}
        for key, val in solution.get(end_key, {}).items():
            idx = _parse_index_key(key)
            if idx:
                w_vals[idx[0]] = float(val)

        crew_assign = {d: d for d in active_duties}

        # ── Build duty details ──
        duties = []
        total_driving_all = 0
        total_idle_all = 0
        total_trips_covered = 0
        constraint_violations = []

        for d in sorted(active_duties):
            trip_ids = sorted(duty_trips.get(d, []))
            if not trip_ids:
                continue

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

            start_min = float(d_trips["trip_dep_time"].min()) - prep_time
            end_min = float(d_trips["trip_arr_time"].max()) + cleanup_time
            total_stay = end_min - start_min
            total_work = total_driving + prep_time + cleanup_time
            idle = total_stay - total_work

            violations = []
            if total_driving > max_driving:
                violations.append(f"운전시간 초과: {total_driving:.0f}/{max_driving:.0f}분")
            if total_work > max_work:
                violations.append(f"근무시간 초과: {total_work:.0f}/{max_work:.0f}분")
            if total_stay > max_stay:
                violations.append(f"체류시간 초과: {total_stay:.0f}/{max_stay:.0f}분")
            if violations:
                constraint_violations.extend([(d, v) for v in violations])

            duties.append({
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
            })

            total_driving_all += total_driving
            total_idle_all += max(idle, 0)
            total_trips_covered += len(trip_ids)

        # ── KPI ──
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

        if obj_type == "duty_count_min":
            kpi["duty_reduction_vs_trips"] = round((1 - n_duties / total_trips) * 100, 1) if total_trips > 0 else 0
            kpi["earliest_start"] = _min_to_hhmm(min(d["start_time_min"] for d in duties)) if duties else "--:--"
            kpi["latest_end"] = _min_to_hhmm(max(d["end_time_min"] for d in duties)) if duties else "--:--"

        # ── Constraint status ──
        model_constraints = math_model.get("constraints", [])
        hard_constraints = [
            c for c in model_constraints
            if c.get("category", c.get("priority", "hard")) == "hard"
        ]
        soft_constraints = [
            c for c in model_constraints
            if c.get("category", c.get("priority", "hard")) == "soft"
        ]

        constraint_status = []
        for c in hard_constraints:
            cname = c.get("name", c.get("id", ""))
            entry = self._check_hard_constraint(cname, duties, params, total_trips, total_trips_covered)
            if entry:
                constraint_status.append(entry)

        # Fallback: no model constraints → basic 4
        if not constraint_status:
            constraint_status = [
                {"name": "운전시간 상한", "limit": f"{max_driving:.0f}분",
                 "max_actual": f"{max(d['total_driving_min'] for d in duties):.0f}분" if duties else "-",
                 "satisfied": all(d["total_driving_min"] <= max_driving for d in duties),
                 "constraint_type": "parametric"},
                {"name": "근무시간 상한", "limit": f"{max_work:.0f}분",
                 "max_actual": f"{max(d['total_work_min'] for d in duties):.0f}분" if duties else "-",
                 "satisfied": all(d["total_work_min"] <= max_work for d in duties),
                 "constraint_type": "parametric"},
                {"name": "체류시간 상한", "limit": f"{max_stay:.0f}분",
                 "max_actual": f"{max(d['total_stay_min'] for d in duties):.0f}분" if duties else "-",
                 "satisfied": all(d["total_stay_min"] <= max_stay for d in duties),
                 "constraint_type": "parametric"},
                {"name": "트립 커버리지", "limit": f"{total_trips}개",
                 "max_actual": f"{total_trips_covered}개",
                 "satisfied": total_trips_covered == total_trips,
                 "constraint_type": "structural"},
            ]

        soft_constraint_status = self._build_soft_constraint_status(soft_constraints)

        warnings = []
        if total_trips_covered < total_trips:
            warnings.append(f"미배정 트립 {total_trips - total_trips_covered}개 존재")
        if constraint_violations:
            warnings.append(f"제약 위반 {len(constraint_violations)}건 감지")

        return {
            "objective_type": obj_type,
            "objective_label": obj_label,
            "objective_value": objective_value,
            "solver_id": solver_id,
            "solver_name": solver_name,
            "status": status,
            "kpi": kpi,
            "duties": duties,
            "constraint_status": constraint_status,
            "soft_constraint_status": soft_constraint_status,
            "warnings": warnings,
        }

    def save_artifacts(
        self,
        project_dir: str,
        solution: Dict[str, Any],
        interpreted: Dict[str, Any],
        solver_id: str,
    ) -> Dict[str, str]:
        # Base artifacts (solution + interpretation JSON)
        saved = super().save_artifacts(project_dir, solution, interpreted, solver_id)
        results_dir = os.path.join(project_dir, "results")

        # Duty schedule CSV
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

        # KPI JSON
        kpi_path = os.path.join(results_dir, f"kpi_{solver_id}.json")
        with open(kpi_path, "w", encoding="utf-8") as f:
            json.dump(interpreted.get("kpi", {}), f, ensure_ascii=False, indent=2)
        saved["kpi"] = kpi_path

        logger.info(f"Artifacts saved: {list(saved.keys())} -> {results_dir}")
        return saved


# ── Register railway interpreter ──
register_interpreter("railway", RailwayResultInterpreter)
register_interpreter("crew", RailwayResultInterpreter)


# ============================================================
# Public API (backward-compatible module-level functions)
# ============================================================

def interpret_result(
    solution: Dict[str, Any],
    math_model: Dict[str, Any],
    project_dir: str,
    solver_id: str = "",
    solver_name: str = "",
    status: str = "",
    objective_value: float = None,
    params: Dict[str, Any] = None,
    policy_snapshot: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible entry point.
    Dispatches to domain-specific interpreter based on math_model.domain.
    """
    domain = math_model.get("domain", "railway")
    interp = get_interpreter(domain)
    return interp.interpret(
        solution=solution,
        math_model=math_model,
        project_dir=project_dir,
        solver_id=solver_id,
        solver_name=solver_name,
        status=status,
        objective_value=objective_value,
        params=params,
        policy_snapshot=policy_snapshot,
    )


def save_artifacts(
    project_dir: str,
    solution: Dict[str, Any],
    interpreted: Dict[str, Any],
    solver_id: str,
    domain: str = "railway",
) -> Dict[str, str]:
    """
    Backward-compatible entry point for artifact saving.
    """
    interp = get_interpreter(domain)
    return interp.save_artifacts(project_dir, solution, interpreted, solver_id)
