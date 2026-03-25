"""
engine/template_model_builder.py
────────────────────────────────
경로 A/B: 도메인 템플릿(constraints.yaml) 기반 수학 모델 조립기.

- 경로 A: confirmed_constraints의 모든 제약이 템플릿에 매칭 → LLM 불필요
- 경로 B: 일부 미매칭 → 매칭분은 템플릿, 미매칭분은 LLM에 위임 (TODO)
- 경로 C: 템플릿 자체가 없음 → 기존 LLM 전체 생성 (이 파일 불사용)
"""

import os
import logging
import yaml
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def load_domain_template(domain: str) -> Optional[Dict]:
    """knowledge/domains/{domain}/constraints.yaml 로드. 없으면 None."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "knowledge", "domains", domain, "constraints.yaml")
    if not os.path.exists(path):
        logger.info(f"No domain template found: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        logger.info(f"Domain template loaded: {path}")
        return data
    except Exception as e:
        logger.warning(f"Failed to load domain template: {e}")
        return None


def classify_route(
    confirmed_constraints: Dict,
    template: Dict,
) -> Tuple[str, List[str], List[str]]:
    """
    confirmed_constraints의 제약 ID를 템플릿과 대조하여 경로를 결정.

    Returns:
        (route, matched_ids, unmatched_ids)
        route: "A" | "B" | "C"
    """
    template_constraint_ids = set(template.get("constraints", {}).keys())

    all_confirmed_ids = []
    for category in ["hard", "soft"]:
        section = confirmed_constraints.get(category, {})
        if isinstance(section, dict):
            all_confirmed_ids.extend(section.keys())
        elif isinstance(section, list):
            all_confirmed_ids.extend(section)

    matched = [cid for cid in all_confirmed_ids if cid in template_constraint_ids]
    unmatched = [cid for cid in all_confirmed_ids if cid not in template_constraint_ids]

    if not unmatched:
        route = "A"
    elif matched:
        route = "B"
    else:
        route = "C"

    logger.info(
        f"Route classification: {route} "
        f"(matched={len(matched)}, unmatched={len(unmatched)}, "
        f"unmatched_ids={unmatched})"
    )
    return route, matched, unmatched


def build_model_from_template(
    template: Dict,
    confirmed_constraints: Dict,
    confirmed_problem: Dict,
    phase1_summary: Optional[Dict] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    constraints.yaml 템플릿에서 수학 모델 JSON을 조립.

    Returns:
        generate_math_model()과 동일한 포맷:
        {"success": bool, "model": {...}, "validation": {...}, "error": str|None}
    """
    try:
        model = {}

        # ─── 1. Sets 조립 ───
        template_sets = template.get("sets", {})
        model_sets = []
        for set_id, set_info in template_sets.items():
            s = {
                "id": set_id,
                "name": set_info.get("description", set_id),
                "description": set_info.get("description", ""),
            }
            source = set_info.get("source", "")
            if source.endswith(".csv"):
                s["source_file"] = f"normalized/{source}"
                s["source_column"] = set_info.get("column", "")
            elif source.endswith(".json"):
                s["source_file"] = f"normalized/{source}"
                s["source_type"] = "explicit"
                # overlap_pairs.json의 크기를 phase1_summary에서 가져오기
                if set_id == "overlap_pairs" and phase1_summary:
                    op_count = phase1_summary.get("overlap_pairs", 0)
                    if op_count > 0:
                        s["size"] = op_count
            elif source == "range":
                s["source_type"] = "range"
                # 기본 크기 설정: phase1_summary에서 가져오거나 default
                default_size = set_info.get("default_size", 96)
                if phase1_summary and set_id in ("J", "D"):
                    trip_count = phase1_summary.get("timetable_trips", 0)
                    if trip_count > 0:
                        # 경험적: trip 수의 절반을 상한으로
                        s["size"] = max(trip_count // 2, default_size)
                    else:
                        s["size"] = default_size
                else:
                    s["size"] = default_size
            model_sets.append(s)
        model["sets"] = model_sets

        # ─── 2. Variables 조립 ───
        template_vars = template.get("variables", {})
        model_variables = []
        for var_id, var_info in template_vars.items():
            v = {
                "id": var_id,
                "name": var_info.get("description", var_id),
                "type": var_info.get("type", "binary"),
                "indices": var_info.get("indices", []),
                "description": var_info.get("description", ""),
            }
            # bounds 설정
            if v["type"] == "binary":
                v["lower_bound"] = 0
                v["upper_bound"] = 1
            elif v["type"] == "integer":
                v["lower_bound"] = 0
                v["upper_bound"] = var_info.get("upper_bound", 1440)  # constraints.yaml 값 우선
            if var_info.get("aliases"):
                v["aliases"] = var_info["aliases"]
            model_variables.append(v)
        model["variables"] = model_variables

        # ─── 3. Constraints 조립 ───
        template_constraints = template.get("constraints", {})
        model_constraints = []
        all_parameter_ids = set()

        for category in ["hard", "soft"]:
            section = confirmed_constraints.get(category, {})
            constraint_ids = section.keys() if isinstance(section, dict) else section

            for cid in constraint_ids:
                tc = template_constraints.get(cid)
                if not tc:
                    logger.warning(f"Constraint '{cid}' not found in template, skipping")
                    continue

                constraint = {
                    "name": cid,
                    "description": tc.get("description", ""),
                    "expression": tc.get("expression_template", ""),
                    "for_each": tc.get("for_each", ""),
                    "category": category,
                }

                # structured 블록이 있으면 포함
                if tc.get("structured"):
                    constraint["lhs"] = tc["structured"].get("lhs", {})
                    constraint["operator"] = tc["structured"].get("operator", "<=")
                    constraint["rhs"] = tc["structured"].get("rhs", {})
                else:
                    # structured 없으면 expression_template에서 operator 추출
                    expr = tc.get("expression_template", "")
                    for op in ["<=", ">=", "==", "!=", "<", ">"]:
                        if op in expr:
                            constraint["operator"] = op
                            break
                    else:
                        constraint["operator"] = "<="  # 기본값

                # objective_operator_override 적용 (ConstraintOperatorResolver)
                from engine.compiler.operator_resolver import ConstraintOperatorResolver
                resolver = ConstraintOperatorResolver(objective_name=obj_target)
                resolved_op = resolver.resolve(cid, tc)
                original_op = constraint.get("operator", "==")
                if resolved_op != original_op:
                    constraint["operator"] = resolved_op
                    # expression_template의 연산자도 교체 (컴파일러가 expression에서 파싱)
                    expr = constraint.get("expression", "")
                    if original_op in expr:
                        constraint["expression"] = expr.replace(original_op, resolved_op, 1)
                    logger.info(
                        f"Constraint '{cid}': operator '{original_op}' → '{resolved_op}' "
                        f"(objective: {obj_target})"
                    )

                # soft 제약의 penalty 정보
                if category == "soft":
                    if tc.get("penalty_var"):
                        constraint["penalty_var"] = tc["penalty_var"]
                    if tc.get("penalty_weight"):
                        constraint["weight"] = tc["penalty_weight"]

                model_constraints.append(constraint)

                # 파라미터 수집
                params = tc.get("parameters", [])
                if isinstance(params, list):
                    all_parameter_ids.update(params)
                elif isinstance(params, dict):
                    all_parameter_ids.update(params.keys())

        model["constraints"] = model_constraints

        # ─── 4. Parameters 조립 ───
        # preparation_time/cleanup_time/max_driving_time 등의 표현식에서
        # trip_dep_time[i], trip_arr_time[i], trip_duration[i]를 계수로 사용하는데,
        # 이 값들이 parameters에 없으면 expression_parser가 0으로 처리하여
        # duty_start[j] <= -prep_minutes → INFEASIBLE 발생.
        _IMPLICIT_TRIP_PARAMS = {
            "trip_dep_time": "trip_dep_time",
            "trip_arr_time": "trip_arr_time",
            "trip_duration": "trip_duration",
        }
        for _tp in _IMPLICIT_TRIP_PARAMS:
            all_parameter_ids.discard(_tp)  # 중복 방지 후 아래서 재추가

        model_parameters = []
        for pid in sorted(all_parameter_ids):
            p = {
                "id": pid,
                "name": pid,
                "description": "",
                "type": "numeric",
            }
            # constraints.yaml에서 파라미터 상세정보 추출
            for cid, tc in template_constraints.items():
                tc_params = tc.get("parameters", {})
                if isinstance(tc_params, dict) and pid in tc_params:
                    pinfo = tc_params[pid]
                    if isinstance(pinfo, dict):
                        p["description"] = pinfo.get("description", "")
                        if pinfo.get("typical_range"):
                            p["typical_range"] = pinfo["typical_range"]
                        if pinfo.get("detection_hints"):
                            p["detection_hints"] = pinfo["detection_hints"]
                    break
            model_parameters.append(p)

        # 트립 데이터 컬럼: normalized/trips.csv에서 인덱스 바인딩
        # key_column을 지정하여 DataBinder가 {trip_id: value} dict로 반환하도록 함
        for _tp_id, _tp_col in _IMPLICIT_TRIP_PARAMS.items():
            model_parameters.append({
                "id": _tp_id,
                "name": _tp_id,
                "description": f"trips.csv의 {_tp_col} 컬럼 (indexed by trip_id)",
                "type": "numeric",
                "source_file": "normalized/trips.csv",
                "source_column": _tp_col,
                "key_column": "trip_id",
            })
        logger.info(f"Implicit trip params added: {list(_IMPLICIT_TRIP_PARAMS.keys())}")
        # 자동 계산 가능한 파라미터 기본값 설정
        # ── 자동 설정 파라미터 (사용자 데이터에 없는 항목) ──
        _auto_defaults = {}
        _auto_inform = []  # 사용자에게 알려줄 자동 설정 내역

        # 1) big_m: 수학적 상수 (사용자에게 숨김)
        _auto_defaults["big_m"] = 10000

        # 2) night_threshold: night_duty_start_earliest와 동일 의미
        _auto_defaults["night_threshold"] = 1020
        _auto_inform.append({
            "param": "night_threshold",
            "value": 1020,
            "reason": "야간 최소 출고시간(17:00)과 동일한 값을 주야간 구분 기준으로 적용했습니다",
            "category": "auto_matched",
        })

        # 3) min_break_minutes: 업계 관행
        _auto_defaults["min_break_minutes"] = 30
        _auto_inform.append({
            "param": "min_break_minutes",
            "value": 30,
            "reason": "필수 휴식시간: 업계 관행 기준 30분을 적용했습니다",
            "category": "industry_default",
        })

        # 4) min_meal_break_minutes: 업계 관행
        _auto_defaults["min_meal_break_minutes"] = 30
        _auto_inform.append({
            "param": "min_meal_break_minutes",
            "value": 30,
            "reason": "식사시간 보장: 업계 관행 기준 30분을 적용했습니다",
            "category": "industry_default",
        })

        # 5) max_trips_per_crew: 데이터 기반 자동 계산
        _max_tpc = 10  # 기본값
        if phase1_summary:
            trip_count = phase1_summary.get("timetable_trips", 0)
            if trip_count > 0:
                est_duties = max(trip_count // 7, 30)
                _max_tpc = max(trip_count // est_duties + 3, 8)
        _auto_defaults["max_trips_per_crew"] = _max_tpc
        _auto_inform.append({
            "param": "max_trips_per_crew",
            "value": _max_tpc,
            "reason": f"운행 데이터 기반 자동 계산 (crew당 최대 트립 수: {_max_tpc}개)",
            "category": "auto_computed",
        })

        # ── Step 1: confirmed_problem 파라미터 값 병합 (최우선) ──
        # problem_definition 단계에서 parameters.csv + 제약조건 values로 수집된 사용자 데이터
        cp_params = confirmed_problem.get("parameters", {})
        if cp_params:
            for p in model_parameters:
                pid = p["id"]
                cp_val = cp_params.get(pid)
                if cp_val is None:
                    continue
                if isinstance(cp_val, dict):
                    val = cp_val.get("value")
                    src = cp_val.get("source", "")
                else:
                    val = cp_val
                    src = ""
                if val is not None:
                    p["default_value"] = val
                    p["value"] = val
                    p["source"] = src or "confirmed_problem"
                    logger.info(f"Parameter '{pid}' = {val} (source: {src or 'confirmed_problem'})")

        # ── Step 1b: clarification 답변의 known runtime parameter 병합 ──
        # model_parameters에 선언되지 않았지만 clarification으로 확정된 파라미터 추가
        KNOWN_RUNTIME_PARAMS = {
            "is_overnight_crew", "min_night_sleep_minutes", "sleep_counts_as_work",
            "day_crew_count", "night_crew_count", "total_duties",
        }
        if cp_params:
            model_param_ids = {p["id"] for p in model_parameters}
            for pid, pval in cp_params.items():
                if pid in KNOWN_RUNTIME_PARAMS and pid not in model_param_ids:
                    _val = pval.get("value") if isinstance(pval, dict) else pval
                    _src = pval.get("source", "user_clarification") if isinstance(pval, dict) else "user_clarification"
                    model_parameters.append({
                        "id": pid, "type": "scalar",
                        "default_value": _val, "value": _val,
                        "source": _src,
                    })
                    model_param_ids.add(pid)
                    logger.info(f"Parameter '{pid}' = {_val} (source: {_src})")

        # ── Step 2: auto_defaults는 아직 값이 없는 파라미터에만 적용 ──
        for p in model_parameters:
            pid = p["id"]
            if pid in _auto_defaults and not p.get("value") and not p.get("default_value"):
                p["default_value"] = _auto_defaults[pid]
                p["value"] = _auto_defaults[pid]
                p["auto_computed"] = True
                logger.info(f"Parameter '{pid}' = {_auto_defaults[pid]} (source: auto_default)")

        model["parameters"] = model_parameters

        # ─── 5. Objective 조립 ───
        template_objectives = template.get("objectives", {})
        cp_objective = confirmed_problem.get("objective", {})
        obj_target = cp_objective.get("target", "min_duties")

        # soft 제약이 있으면 penalty 포함 목적함수 사용
        has_soft = bool(confirmed_constraints.get("soft", {}))

        # 목적함수 매칭: confirmed_problem의 target을 우선 사용
        obj_template = None
        if obj_target in template_objectives:
            # 사용자가 확정한 목적함수가 템플릿에 존재
            obj_template = template_objectives[obj_target]
            logger.info(f"Objective matched by confirmed target: {obj_target}")
        elif has_soft and f"{obj_target}_with_penalties" in template_objectives:
            # target + "_with_penalties" 변형 확인
            obj_template = template_objectives[f"{obj_target}_with_penalties"]
            logger.info(f"Objective matched with penalties variant: {obj_target}_with_penalties")

        # fallback: 기본 목적함수
        if obj_template is None:
            if has_soft and "minimize_duties_with_penalties" in template_objectives:
                obj_template = template_objectives["minimize_duties_with_penalties"]
            elif "minimize_duties" in template_objectives:
                obj_template = template_objectives["minimize_duties"]
            else:
                obj_template = next(iter(template_objectives.values()), {})
            logger.info(f"Objective fallback used (target '{obj_target}' not found in template)")

        model["objective"] = {
            "type": obj_template.get("type", "minimize"),
            "description": obj_template.get("description_ko", obj_template.get("description", "")),
            "expression": obj_template.get("expression", ""),
            "alternatives": [],
        }

        # 대안 목적함수 추가
        for obj_id, obj_info in template_objectives.items():
            if obj_info.get("expression") != obj_template.get("expression"):
                model["objective"]["alternatives"].append({
                    "type": obj_info.get("type", "minimize"),
                    "description": obj_info.get("description_ko", obj_info.get("description", "")),
                })

        # ─── 6. Metadata 조립 ───
        set_sizes = {}
        for s in model_sets:
            if s.get("size"):
                set_sizes[s["id"]] = s["size"]
            elif phase1_summary:
                if s["id"] == "I":
                    set_sizes["I"] = phase1_summary.get("timetable_trips", 0)

        # 변수 수 추정
        est_vars = 0
        i_size = set_sizes.get("I", phase1_summary.get("timetable_trips", 320) if phase1_summary else 320)
        j_size = set_sizes.get("J", 96)
        # total_duties가 확정되면 J 크기를 반영 (J의 default_size보다 실제 duty 수가 정확)
        total_duties = confirmed_problem.get("total_duties")
        if total_duties is not None:
            td = int(total_duties.get("value", 0) if isinstance(total_duties, dict) else total_duties)
            if 0 < td < j_size:
                j_size = td
        for v in model_variables:
            indices = v.get("indices", [])
            if len(indices) == 2:
                est_vars += i_size * j_size
            elif len(indices) == 1:
                if indices[0] in ("I", "T"):
                    est_vars += i_size
                else:
                    est_vars += j_size
            else:
                est_vars += 1

        # domain을 top-level에 설정 (PolicyEngine 등이 math_model.get("domain") 사용)
        _domain = (
            template.get("domain", "")
            or confirmed_problem.get("domain", "")
            or confirmed_problem.get("detected_domain", "")
            or ""
        )
        model["domain"] = _domain

        model["metadata"] = {
            "estimated_variable_count": est_vars,
            "estimated_constraint_count": len(model_constraints),
            "variable_types_used": list({v["type"] for v in model_variables}),
            "data_files_required": list({
                s.get("source_file", "") for s in model_sets if s.get("source_file")
            }),
            "generation_method": "template",
            "skip_struct_fix": True,
            "domain": _domain,
            "template_version": template.get("version", ""),
        }

        # 자동 설정 파라미터 inform 정보를 metadata에 저장
        model["metadata"]["auto_param_inform"] = _auto_inform

        model["problem_name"] = confirmed_problem.get("objective", {}).get(
            "description", "철도 승무원 스케줄링"
        )

        # ─── 7. Validation ───
        errors = []
        warnings = []

        if not model_constraints:
            errors.append("제약조건이 하나도 조립되지 않았습니다")
        if not model_variables:
            errors.append("변수가 하나도 조립되지 않았습니다")

        # 파라미터 값 미설정 경고 (Gate 2에서 바인딩 예정)
        for p in model_parameters:
            if not p.get("value") and not p.get("default_value"):
                warnings.append(f"파라미터 '{p['id']}'의 값이 미설정 (Gate 2에서 자동 바인딩 예정)")

        logger.info(
            f"Template model built: "
            f"sets={len(model_sets)}, vars={len(model_variables)}, "
            f"constraints={len(model_constraints)}, params={len(model_parameters)}, "
            f"est_vars={est_vars}"
        )

        return {
            "success": len(errors) == 0,
            "model": model,
            "validation": {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
            },
            "error": errors[0] if errors else None,
        }

    except Exception as e:
        logger.error(f"Template model build failed: {e}", exc_info=True)
        return {
            "success": False,
            "model": None,
            "validation": None,
            "error": f"템플릿 모델 조립 실패: {str(e)}",
        }
