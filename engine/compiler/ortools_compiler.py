import json
import logging
import re
from typing import Dict, Any, List, Optional
from .base import BaseCompiler, CompileResult
from .struct_builder import BuildContext, build_constraint, build_constraints_batch, apply_constraint_cpsat, apply_constraint_lp, eval_node
from .expression_parser import parse_and_apply_expression

logger = logging.getLogger(__name__)

# ── Soft Constraint 스케일링 상수 ──
# MAX_SLACK: slack 변수 상한 (분 단위, 24시간)
# NORMALIZE: 패널티 정규화 기준 (5시간 = 300분)
_SOFT_MAX_SLACK = 1440
_SOFT_NORMALIZE = 300


# ── soft constraint weight 캐싱 ──
_soft_weights_cache: Optional[Dict[str, float]] = None


def _load_soft_weights(force_reload: bool = False) -> Dict[str, float]:
    """constraints.yaml에서 soft constraint의 weight 값을 로딩 (모듈 레벨 캐시)"""
    global _soft_weights_cache
    if _soft_weights_cache is not None and not force_reload:
        return _soft_weights_cache

    import os
    try:
        import yaml
    except ImportError:
        _soft_weights_cache = {}
        return _soft_weights_cache
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    domains_dir = os.path.join(base, "knowledge", "domains")
    weights: Dict[str, float] = {}
    if not os.path.isdir(domains_dir):
        _soft_weights_cache = weights
        return _soft_weights_cache
    for dname in os.listdir(domains_dir):
        cpath = os.path.join(domains_dir, dname, "constraints.yaml")
        if not os.path.isfile(cpath):
            continue
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
        except Exception:
            continue
        # v3 format: constraints → {id: {default_category: soft, weight: ...}}
        # legacy format: soft → {id: {weight: ...}}
        constraints = cdata.get("constraints") or {}
        soft_section = cdata.get("soft") or {}
        # v3: constraints 딕셔너리에서 soft 카테고리 추출
        for cid, cdef in constraints.items():
            if isinstance(cdef, dict) and cdef.get("default_category") == "soft":
                weights[cid] = float(cdef.get("weight", 1.0))
        # legacy fallback
        for cid, cdef in soft_section.items():
            if isinstance(cdef, dict):
                weights[cid] = float(cdef.get("weight", 1.0))
    _soft_weights_cache = weights
    return _soft_weights_cache


class ORToolsCompiler(BaseCompiler):
    """OR-Tools CP-SAT / LP 컴파일러 (struct_builder 기반)"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        try:
            var_types = set()
            for v in math_model.get("variables", []):
                var_types.add(self._get_variable_type(v))

            has_continuous = "continuous" in var_types
            if has_continuous:
                # 승무원 스케줄링: 모든 시간값은 분 단위 정수
                # continuous를 integer로 변환하여 CP-SAT 사용
                for v in math_model.get("variables", []):
                    if v.get("type") == "continuous":
                        logger.info(f"Auto-converting variable {v.get('id')} from continuous to integer")
                        v["type"] = "integer"
                return self._compile_cp_sat(math_model, bound_data, **kwargs)
            else:
                return self._compile_cp_sat(math_model, bound_data, **kwargs)

        except Exception as e:
            logger.error(f"ORTools compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _compile_cp_sat(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """CP-SAT 모델 생성 (struct_builder 연동)"""
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()
        var_map = {}
        total_vars = 0
        warnings = []

        # 1. 변수 생성 (기존 로직 유지)
        for var_def in math_model.get("variables", []):
            vid = var_def.get("id", "")
            vtype = self._get_variable_type(var_def)
            indices = var_def.get("indices", [])

            if not indices:
                if vtype == "binary":
                    var_map[vid] = model.new_bool_var(vid)
                else:
                    lb = int(var_def.get("lower_bound") or 0)
                    ub = int(var_def.get("upper_bound") or 1000000)
                    var_map[vid] = model.new_int_var(lb, ub, vid)
                total_vars += 1
            else:
                combos = self._compute_set_product(indices, bound_data)
                var_map[vid] = {}
                for combo in combos:
                    key = tuple(str(c) for c in combo)
                    name = f"{vid}_{'_'.join(key)}"
                    if vtype == "binary":
                        var_map[vid][key] = model.new_bool_var(name)
                    else:
                        lb = int(var_def.get("lower_bound") or 0)
                        ub = int(var_def.get("upper_bound") or 1000000)
                        var_map[vid][key] = model.new_int_var(lb, ub, name)
                    total_vars += 1

                if not combos:
                    warnings.append(f"Variable {vid}: no index combinations generated")

        # DEBUG: sets 정보
        for s in math_model.get("sets", []):
            logger.info(f"DEBUG set '{s.get('id')}': source_type={s.get('source_type','N/A')}, size={s.get('size','N/A')}, source_file={s.get('source_file','N/A')}, source_column={s.get('source_column','N/A')}")
        logger.info(f"CP-SAT: created {total_vars} variables")

        # ── 1b. Duty Event 변수 (비활성화) ──
        # Set Partitioning 전환으로 event 변수는 SP 경로에서 불필요.
        # 기존 I×J 경로에서는 CP-SAT presolve와 충돌하여 비활성화.
        # 향후 D-Wave 등 다른 solver에서 필요 시 재활성화.
        # event_constraint_count = self._build_duty_event_variables(
        #     model, var_map, math_model, bound_data, warnings
        # )
        # total_vars += event_constraint_count.get("vars_added", 0)

        # 2. 제약조건 - struct_builder 사용 (3단계 fallback)
        param_map = bound_data.get("parameters", {})
        set_map = bound_data.get("sets", {})
        
        # --- overlap_pairs 로딩 (project_id 기반) ---
        import os as _os2
        _project_id = kwargs.get('project_id', '')
        if 'overlap_pairs' not in set_map or len(set_map.get('overlap_pairs', [])) < 2:
            _op_path = _os2.path.join('uploads', str(_project_id), 'normalized', 'overlap_pairs.json')
            if _os2.path.exists(_op_path):
                try:
                    import json as _ojson
                    with open(_op_path, encoding='utf-8') as _opf:
                        _op_data = _ojson.load(_opf)
                    if isinstance(_op_data, list) and len(_op_data) > 0:
                        set_map['overlap_pairs'] = [tuple(p) for p in _op_data]
                        logger.info(f"CP-SAT overlap_pairs loaded: {len(set_map['overlap_pairs'])} pairs from {_op_path}")
                except Exception as _ope:
                    logger.warning(f"CP-SAT overlap_pairs load failed: {_ope}")
            else:
                logger.warning(f"overlap_pairs.json not found at {_op_path}")
        # --- end overlap_pairs ---

        ctx = BuildContext(var_map, param_map, set_map, model=model)

        logger.info(f"BuildContext - sets: {list(set_map.keys())}, sizes: {[len(v) for v in set_map.values()]}")
        logger.info(f"BuildContext - params: {list(param_map.keys())[:20]}")
        logger.info(f"BuildContext - vars: {list(var_map.keys())}")

        total_constraints = 0
        constraint_defs = math_model.get("constraints", [])
        constraint_info = []  # 적용된 제약조건 메타 [(name, category, count, method)]

        # ★ NEW: soft constraint 처리를 위한 준비
        soft_weights = _load_soft_weights()
        soft_slack_vars = []       # (slack_var, weight) 튜플 리스트
        soft_applied_count = 0

        for con_def in constraint_defs:
            cname = con_def.get("name", con_def.get("id", "unknown"))
            # DEBUG: 제약 JSON 구조 출력
            _keys = list(con_def.keys())
            _has_lhs = "lhs" in con_def
            _has_rhs = "rhs" in con_def
            _has_expr = "expression" in con_def
            logger.debug(f"DEBUG constraint '{cname}': keys={_keys}, has_lhs={_has_lhs}, has_rhs={_has_rhs}, has_expr={_has_expr}")
            if _has_lhs:
                logger.debug(f"  lhs={json.dumps(con_def['lhs'], ensure_ascii=False, default=str)[:200]}")
            if _has_rhs:
                logger.debug(f"  rhs={json.dumps(con_def['rhs'], ensure_ascii=False, default=str)[:200]}")
            if _has_expr:
                logger.debug(f"  expr={con_def['expression'][:150]}")
            category = con_def.get("category", con_def.get("priority", "hard"))
            expr = con_def.get("expression", "")

            # ★ CHANGED: soft constraint → 슬랙 변수 + 패널티 처리
            if category == "soft":
                soft_result = self._apply_soft_constraint_cpsat(
                    model, con_def, ctx, var_map, soft_weights
                )
                if soft_result is not None:
                    slack_count, slack_entries = soft_result
                    total_constraints += slack_count
                    soft_slack_vars.extend(slack_entries)
                    soft_applied_count += 1
                    constraint_info.append({"name": cname, "category": "soft", "count": slack_count, "method": "soft_slack"})
                    logger.info(f"Soft constraint '{cname}': {slack_count} instances applied")
                else:
                    constraint_info.append({"name": cname, "category": "soft", "count": 0, "method": "skipped"})
                    warnings.append(f"Soft constraint {cname}: could not apply, skipped")
                continue

            # operator가 비교연산자가 아닌 경우 (*, +, - 등) expression에서 재파싱 시도
            op_field = con_def.get("operator", "==")
            if op_field not in ("==", "<=", ">=", "<", ">", "!="):
                # expression 필드에 비교연산자가 있으면 expression 기반으로 재구성
                expr = con_def.get("expression", "")
                import re as _re
                expr_op_match = _re.search(r'(<=|>=|==|!=|<|>)', expr)
                if expr_op_match:
                    logger.info(f"Constraint '{cname}': operator='{op_field}' is not comparison, reparsing from expression")
                    expr_op = expr_op_match.group(1)
                    expr_parts = _re.split(r'(<=|>=|==|!=)', expr, maxsplit=1)
                    if len(expr_parts) == 3:
                        con_def = dict(con_def)
                        con_def["operator"] = expr_op
                        # expression 기반 fallback으로 넘김
                        has_struct = False
                else:
                    warnings.append(f"Constraint {cname}: operator '{op_field}' is not a comparison, skipped")
                    continue

            # Fallback 1: 구조화 필드 (lhs/operator/rhs)
            # (1) expression 문자열이 있으면 expression_parser 우선 사용
            expr_str = con_def.get("expression", "").strip()
            for_each_str = con_def.get("for_each", "")
            parsed_count = 0

            if expr_str and any(op in expr_str for op in ["<=", ">=", "=="]):
                try:
                    parsed_count = parse_and_apply_expression(
                        model, expr_str, for_each_str, ctx, var_map
                    )
                    if parsed_count > 0:
                        total_constraints += parsed_count
                        constraint_info.append({"name": cname, "category": category, "count": parsed_count, "method": "expression_parser"})
                        logger.info(f"Constraint '{cname}': {parsed_count} instances (expression_parser)")
                        continue
                except Exception as e:
                    warnings.append(f"Constraint {cname}: expression_parser error ({e})")

            # (2) structured JSON (lhs/rhs) 처리
            has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None

            if has_struct:
                try:
                    results = build_constraint(con_def, ctx)
                    for lhs_val, op, rhs_val in results:
                        if apply_constraint_cpsat(model, lhs_val, op, rhs_val):
                            parsed_count += 1
                    if parsed_count > 0:
                        logger.info(f"Constraint '{cname}': {parsed_count} instances (structured)")
                        total_constraints += parsed_count
                        constraint_info.append({"name": cname, "category": category, "count": parsed_count, "method": "structured"})
                        continue
                    else:
                        logger.warning(f"Constraint '{cname}' FAILED structured - lhs={json.dumps(con_def.get('lhs'), ensure_ascii=False, default=str)[:300]}")
                        logger.warning(f"Constraint '{cname}' FAILED structured - rhs={json.dumps(con_def.get('rhs'), ensure_ascii=False, default=str)[:300]}")
                        logger.warning(f"Constraint '{cname}' FAILED structured - operator={con_def.get('operator')}, for_each={con_def.get('for_each')}")
                        warnings.append(f"Constraint {cname}: structured build returned 0 valid constraints, trying fallback")
                except Exception as e:
                    warnings.append(f"Constraint {cname}: structured build error ({e}), trying fallback")

            # Fallback 2: 기존 정규식 패턴 매칭
            parsed_count = self._parse_constraint_cpsat_legacy(model, var_map, con_def, bound_data)
            if parsed_count > 0:
                total_constraints += parsed_count
                constraint_info.append({"name": cname, "category": category, "count": parsed_count, "method": "legacy_regex"})
                logger.info(f"Constraint '{cname}': {parsed_count} instances (legacy regex)")
            else:
                constraint_info.append({"name": cname, "category": category, "count": 0, "method": "failed"})
                warnings.append(f"Constraint {cname}: all parse methods failed: {expr[:80]}")

        logger.info(f"CP-SAT: created {total_constraints} constraints (soft applied: {soft_applied_count})")

        # 3. 목적함수 - struct_builder 시도 후 fallback
        #    ★ CHANGED: soft penalty를 목적함수에 합산
        obj = math_model.get("objective", {})
        obj_parsed = self._parse_objective_cpsat_with_soft(
            model, var_map, obj, ctx, soft_slack_vars
        )
        if not obj_parsed:
            warnings.append("Objective: could not parse, using default minimize sum + soft penalty")

        return CompileResult(
            success=True,
            solver_model=model,
            solver_type="ortools_cp",
            variable_count=total_vars,
            constraint_count=total_constraints,
            variable_map=var_map,
            warnings=warnings,
            metadata={
                "model_type": "CP-SAT",
                "engine": "ortools",
                "soft_constraints_applied": soft_applied_count,
                "soft_slack_variables": len(soft_slack_vars),
                "constraint_info": constraint_info,
            },
        )

    # ── Window Containment: trip이 duty window 안에 포함 ──
    def _build_duty_event_variables(
        self, model, var_map, math_model, bound_data, warnings
    ) -> Dict:
        """
        Window Containment 모델: min/max를 모델링하지 않는다.

        구조:
          trip_dep[i] >= duty_start[j]  OnlyEnforceIf(x[i,j])
          trip_arr[i] <= duty_end[j]    OnlyEnforceIf(x[i,j])

        minimize(span) → duty_start 최대화, duty_end 최소화 → 자연 수렴

        왜 이 구조가 올바른가:
          - min/max 없음 → aggregation 변수 불필요
          - I×J 제약이지만 "window 포함"이므로 presolve-safe
          - prep/cleanup은 reporting layer에서 처리 (solver 밖)
        """
        set_map = bound_data.get("sets", {})
        param_map = bound_data.get("parameters", {})

        j_set = set_map.get("J", [])
        i_set = set_map.get("I", [])
        if not j_set or not i_set:
            return {"vars_added": 0, "constraints_added": 0}

        trip_dep = param_map.get("trip_dep_abs_minute", param_map.get("trip_dep_time", {}))
        trip_arr = param_map.get("trip_arr_abs_minute", param_map.get("trip_arr_time", {}))
        if not isinstance(trip_dep, dict) or not trip_dep:
            logger.info("Window containment: trip timing data not available, skipping")
            return {"vars_added": 0, "constraints_added": 0}

        x_map = var_map.get("x", {})
        if not x_map:
            return {"vars_added": 0, "constraints_added": 0}

        duty_start_map = var_map.get("duty_start", {})
        duty_end_map = var_map.get("duty_end", {})

        constraints_added = 0

        # ── Window Containment: 할당된 trip은 duty window 안에 ──
        for j in j_set:
            j_key = (str(j),)
            ds = duty_start_map.get(j_key)
            de = duty_end_map.get(j_key)
            if ds is None or de is None:
                continue

            for i in i_set:
                x_key = (str(i), str(j))
                x_var = x_map.get(x_key)
                if x_var is None:
                    continue

                dep_val = trip_dep.get(str(i), trip_dep.get(i))
                arr_val = trip_arr.get(str(i), trip_arr.get(i))
                if dep_val is None or arr_val is None:
                    continue

                dep_int = int(dep_val)
                arr_int = int(arr_val)

                # trip 출발 >= duty 시작 (trip이 duty window 안에)
                model.add(dep_int >= ds).only_enforce_if(x_var)
                # trip 도착 <= duty 종료 (trip이 duty window 안에)
                model.add(arr_int <= de).only_enforce_if(x_var)
                constraints_added += 2

        # prep/cleanup은 solver가 아닌 reporting layer에서 처리
        # actual_start = duty_start - prep, actual_end = duty_end + cleanup
        self._event_prep_cleanup_handled = True

        logger.info(
            f"Window containment: {constraints_added} constraints "
            f"(trip ∈ duty window for {len(j_set)} duties × {len(i_set)} trips)"
        )

        return {"vars_added": 0, "constraints_added": constraints_added}

    # ★ NEW: soft constraint 처리 메서드
    def _apply_soft_constraint_cpsat(self, model, con_def, ctx, var_map, soft_weights):
        """
        soft constraint를 슬랙 변수 + 패널티로 변환.

        원래 제약: lhs <= rhs  (hard)
        변환 후:   lhs <= rhs + slack,  slack >= 0
        목적함수:  ... + weight * slack

        Returns: (constraint_count, [(slack_var, weight), ...]) or None
        """
        from ortools.sat.python import cp_model as cp_module

        cname = con_def.get("name", con_def.get("id", "unknown"))
        has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None

        # lhs/rhs 구조가 없으면 expression 기반으로 시도
        if not has_struct:
            return self._apply_soft_constraint_cpsat_expr(model, con_def, ctx, var_map, soft_weights)

        # weight 결정: constraint 정의 > YAML > 기본값 1.0
        weight = float(con_def.get("weight", soft_weights.get(cname, 1.0)))

        # ★ 스케일 정규화: 주 목적함수(duty 수)와 비교하여 적절한 계수 산출
        #   primary_scale ≈ duty 수, soft 패널티가 전체의 ~10% 수준
        #   MAX_SLACK을 제한하여 solver 성능 보장
        MAX_SLACK = _SOFT_MAX_SLACK
        NORMALIZE = _SOFT_NORMALIZE

        try:
            results = build_constraint(con_def, ctx)
        except Exception as e:
            logger.warning(f"Soft constraint '{cname}' build failed: {e}")
            return None

        if not results:
            return None

        slack_entries = []
        constraint_count = 0
        op = con_def.get("operator", "<=")

        for idx, (lhs_val, orig_op, rhs_val) in enumerate(results):
            slack_name = f"slack_{cname}_{idx}"

            try:
                slack = model.new_int_var(0, MAX_SLACK, slack_name)

                # CP-SAT은 정수만 허용 — float를 int로 변환
                if isinstance(lhs_val, float):
                    lhs_val = int(lhs_val)
                if isinstance(rhs_val, float):
                    rhs_val = int(rhs_val)

                # 제약 방향에 따라 슬랙 추가 방향 결정
                #   lhs <= rhs  →  lhs <= rhs + slack  (slack 완화)
                #   lhs >= rhs  →  lhs + slack >= rhs  (즉, lhs >= rhs - slack)
                #   lhs == rhs  →  |lhs - rhs| <= slack (양방향)
                if orig_op in ("<=", "<"):
                    # lhs - slack <= rhs  →  lhs <= rhs + slack
                    model.Add(lhs_val - slack <= rhs_val)
                elif orig_op in (">=", ">"):
                    # lhs + slack >= rhs  →  lhs >= rhs - slack
                    model.Add(lhs_val + slack >= rhs_val)
                elif orig_op == "==":
                    # 양방향: rhs - slack <= lhs <= rhs + slack
                    model.Add(lhs_val <= rhs_val + slack)
                    model.Add(lhs_val >= rhs_val - slack)
                else:
                    logger.warning(f"Soft constraint '{cname}' idx={idx}: unsupported operator '{orig_op}'")
                    continue

                # 정규화된 weight 계산
                #   alpha = weight / NORMALIZE
                #   실제 패널티 = alpha * slack
                #   CP-SAT은 정수 계수만 지원하므로 스케일링
                scaled_weight = max(1, int(weight * 100 / NORMALIZE))
                slack_entries.append((slack, scaled_weight))
                constraint_count += 1

            except Exception as e:
                logger.warning(f"Soft constraint '{cname}' idx={idx} failed: {e}")
                continue

        if constraint_count > 0:
            logger.info(
                f"Soft constraint '{cname}': {constraint_count} instances, "
                f"weight={weight}, scaled_weight={slack_entries[0][1] if slack_entries else 'N/A'}"
            )
            return (constraint_count, slack_entries)

        return None

    def _apply_soft_constraint_cpsat_expr(self, model, con_def, ctx, var_map, soft_weights):
        """
        expression 문자열 기반 soft constraint 처리.
        expression_parser의 _parse_for_each / _eval_expr를 재사용하여
        lhs_val, rhs_val을 구한 뒤 슬랙 변수를 삽입.
        """
        from engine.compiler.expression_parser import _parse_for_each, _eval_expr

        cname = con_def.get("name", con_def.get("id", "unknown"))
        expr_str = con_def.get("expression", "").strip()
        for_each_str = con_def.get("for_each", "")

        if not expr_str:
            return None

        # 비교 연산자 분리
        orig_op = None
        lhs_str = rhs_str = ""
        for op in ["<=", ">=", "=="]:
            if op in expr_str:
                parts = expr_str.split(op, 1)
                lhs_str, rhs_str = parts[0].strip(), parts[1].strip()
                orig_op = op
                break
        if orig_op is None:
            logger.warning(f"Soft constraint '{cname}': no comparison operator in expression")
            return None

        weight = float(con_def.get("weight", soft_weights.get(cname, 1.0)))
        MAX_SLACK = _SOFT_MAX_SLACK
        NORMALIZE = _SOFT_NORMALIZE

        bindings = _parse_for_each(for_each_str, ctx)
        slack_entries = []
        constraint_count = 0

        for idx, binding in enumerate(bindings):
            try:
                lhs_val = _eval_expr(lhs_str, binding, ctx, var_map, model)
                rhs_val = _eval_expr(rhs_str, binding, ctx, var_map, model)

                if lhs_val is None or rhs_val is None:
                    continue
                if isinstance(lhs_val, float):
                    lhs_val = int(lhs_val)
                if isinstance(rhs_val, float):
                    rhs_val = int(rhs_val)

                slack = model.new_int_var(0, MAX_SLACK, f"slack_{cname}_{idx}")

                if orig_op in ("<=", "<"):
                    model.Add(lhs_val - slack <= rhs_val)
                elif orig_op in (">=", ">"):
                    model.Add(lhs_val + slack >= rhs_val)
                elif orig_op == "==":
                    model.Add(lhs_val <= rhs_val + slack)
                    model.Add(lhs_val >= rhs_val - slack)

                scaled_weight = max(1, int(weight * 100 / NORMALIZE))
                slack_entries.append((slack, scaled_weight))
                constraint_count += 1

            except Exception as e:
                logger.debug(f"Soft constraint '{cname}' expr idx={idx} failed: {e}")
                continue

        if constraint_count > 0:
            logger.info(
                f"Soft constraint '{cname}' (expr): {constraint_count} instances, "
                f"weight={weight}, scaled_weight={slack_entries[0][1] if slack_entries else 'N/A'}"
            )
            return (constraint_count, slack_entries)

        return None

    # ★ NEW: soft penalty를 포함하는 목적함수
    def _parse_objective_cpsat_with_soft(self, model, var_map, obj_def, ctx, soft_slack_vars) -> bool:
        """
        목적함수 = 원래 objective + Σ(scaled_weight * slack)
        """
        from engine.compiler.struct_builder import build_objective

        # Step 1: 원래 목적함수 expression 구성
        obj_type = obj_def.get("type", "minimize")
        obj_expr = None

        # struct_builder로 시도
        try:
            _, obj_val = build_objective(obj_def, ctx)
            if obj_val is not None:
                obj_expr = obj_val
        except Exception as e:
            logger.warning(f"Structured objective failed: {e}")

        # fallback: 모든 변수의 합
        if obj_expr is None:
            all_vars = []
            for vid, v in var_map.items():
                if isinstance(v, dict):
                    all_vars.extend(v.values())
                else:
                    all_vars.append(v)
            if all_vars:
                obj_expr = sum(all_vars)
            else:
                return False

        # Step 2: soft penalty 합산
        if soft_slack_vars:
            soft_penalty = sum(w * s for s, w in soft_slack_vars)
            obj_expr = obj_expr + soft_penalty
            logger.info(
                f"Objective: {obj_type} primary + {len(soft_slack_vars)} soft penalties "
                f"(total scaled weight: {sum(w for _, w in soft_slack_vars)})"
            )
        else:
            logger.info(f"Objective: {obj_type} (no soft penalties)")

        # Step 3: 설정
        try:
            if obj_type == "minimize":
                model.minimize(obj_expr)
            else:
                model.maximize(obj_expr)
            return True
        except Exception as e:
            logger.error(f"Objective setting failed: {e}")
            return False

    def _compile_lp(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """LP/MIP 모델 생성 (struct_builder 연동)"""
        from ortools.linear_solver import pywraplp

        solver = pywraplp.Solver.CreateSolver("SCIP")
        if not solver:
            return CompileResult(success=False, error="SCIP solver not available")

        var_map = {}
        total_vars = 0
        warnings = []

        # 1. 변수 생성 (기존 로직 유지)
        for var_def in math_model.get("variables", []):
            vid = var_def.get("id", "")
            vtype = self._get_variable_type(var_def)
            indices = var_def.get("indices", [])
            lb = float(var_def.get("lower_bound") if var_def.get("lower_bound") is not None else 0)
            ub = float(var_def.get("upper_bound") if var_def.get("upper_bound") is not None else 1e7)

            if not indices:
                if vtype == "binary":
                    var_map[vid] = solver.BoolVar(vid)
                elif vtype == "integer":
                    var_map[vid] = solver.IntVar(int(lb), int(ub), vid)
                else:
                    var_map[vid] = solver.NumVar(lb, ub, vid)
                total_vars += 1
            else:
                combos = self._compute_set_product(indices, bound_data)
                var_map[vid] = {}
                for combo in combos:
                    key = tuple(str(c) for c in combo)
                    name = f"{vid}_{'_'.join(key)}"
                    if vtype == "binary":
                        var_map[vid][key] = solver.BoolVar(name)
                    elif vtype == "integer":
                        var_map[vid][key] = solver.IntVar(int(lb), int(ub), name)
                    else:
                        var_map[vid][key] = solver.NumVar(lb, ub, name)
                    total_vars += 1

        logger.info(f"LP/MIP: created {total_vars} variables")

        # 2. 제약조건 - struct_builder + fallback
        param_map = bound_data.get("parameters", {})
        set_map = bound_data.get("sets", {})

        # --- LP: overlap_pairs 로딩 (project_id 기반) ---
        import os as _os2
        _project_id = kwargs.get('project_id', '')
        if 'overlap_pairs' not in set_map or len(set_map.get('overlap_pairs', [])) < 2:
            _op_path = _os2.path.join('uploads', str(_project_id), 'normalized', 'overlap_pairs.json')
            if _os2.path.exists(_op_path):
                try:
                    import json as _ojson2
                    with open(_op_path, encoding='utf-8') as _opf:
                        _op_data = _ojson2.load(_opf)
                    if isinstance(_op_data, list) and len(_op_data) > 0:
                        set_map['overlap_pairs'] = [tuple(p) for p in _op_data]
                        logger.info(f"LP overlap_pairs loaded: {len(set_map['overlap_pairs'])} pairs from {_op_path}")
                except Exception as _ope:
                    logger.warning(f"LP overlap_pairs load failed: {_ope}")
            else:
                logger.warning(f"LP overlap_pairs.json not found at {_op_path}")
        # --- end LP overlap_pairs ---

        ctx = BuildContext(var_map, param_map, set_map, model=solver)

        logger.info(f"BuildContext - sets: {list(set_map.keys())}, sizes: {[len(v) for v in set_map.values()]}")
        logger.info(f"BuildContext - params: {list(param_map.keys())[:20]}")
        logger.info(f"BuildContext - vars: {list(var_map.keys())}")



        total_constraints = 0

        # ★ NEW: LP soft constraint 처리 준비
        soft_weights = _load_soft_weights()
        soft_slack_vars_lp = []  # (slack_var, weight)
        soft_applied_count = 0

        for con_def in math_model.get("constraints", []):
            cname = con_def.get("name", con_def.get("id", "unknown"))
            category = con_def.get("category", con_def.get("priority", "hard"))

            # ★ CHANGED: LP soft constraint 처리
            if category == "soft":
                soft_result = self._apply_soft_constraint_lp(
                    solver, con_def, ctx, var_map, soft_weights
                )
                if soft_result is not None:
                    slack_count, slack_entries = soft_result
                    total_constraints += slack_count
                    soft_slack_vars_lp.extend(slack_entries)
                    soft_applied_count += 1
                    logger.info(f"LP Soft constraint '{cname}': {slack_count} instances applied")
                else:
                    warnings.append(f"LP Soft constraint {cname}: could not apply, skipped")
                continue

            # (1) expression 문자열이 있으면 expression_parser 우선 사용
            expr_str = con_def.get("expression", "").strip()
            for_each_str = con_def.get("for_each", "")
            parsed_count = 0

            if expr_str and any(op in expr_str for op in ["<=", ">=", "=="]):
                try:
                    parsed_count = parse_and_apply_expression(
                        solver, expr_str, for_each_str, ctx, var_map
                    )
                    if parsed_count > 0:
                        total_constraints += parsed_count
                        logger.info(f"Constraint '{cname}': {parsed_count} instances (expression_parser)")
                        continue
                except Exception as e:
                    warnings.append(f"Constraint {cname}: expression_parser error ({e})")

            # (2) structured JSON fallback
            has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None

            if has_struct:
                try:
                    results = build_constraint(con_def, ctx)
                    for lhs_val, op, rhs_val in results:
                        if apply_constraint_lp(solver, lhs_val, op, rhs_val):
                            parsed_count += 1
                    if parsed_count > 0:
                        total_constraints += parsed_count
                        continue
                except Exception as e:
                    warnings.append(f"Constraint {cname}: structured error ({e})")

            # (3) legacy fallback
            parsed_count = self._parse_constraint_lp_legacy(solver, var_map, con_def, bound_data)
            if parsed_count > 0:
                total_constraints += parsed_count
            else:
                warnings.append(f"Constraint {cname}: all parse methods failed")

        # 3. 목적함수
        #    ★ CHANGED: soft penalty 포함
        obj = math_model.get("objective", {})
        obj_parsed = self._parse_objective_lp_with_soft(
            solver, var_map, obj, bound_data, soft_slack_vars_lp
        )
        if not obj_parsed:
            warnings.append("Objective: could not parse")

        return CompileResult(
            success=True,
            solver_model=solver,
            solver_type="ortools_lp",
            variable_count=total_vars,
            constraint_count=total_constraints,
            variable_map=var_map,
            warnings=warnings,
            metadata={
                "model_type": "LP/MIP",
                "engine": "SCIP",
                "soft_constraints_applied": soft_applied_count,       # ★ NEW
                "soft_slack_variables": len(soft_slack_vars_lp),       # ★ NEW
            },
        )

    # ★ NEW: LP용 soft constraint 처리
    def _apply_soft_constraint_lp(self, solver, con_def, ctx, var_map, soft_weights):
        """LP/MIP용 soft constraint → 슬랙 변수 + 패널티"""
        cname = con_def.get("name", con_def.get("id", "unknown"))
        has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None

        if not has_struct:
            return None

        weight = float(con_def.get("weight", soft_weights.get(cname, 1.0)))
        MAX_SLACK = float(_SOFT_MAX_SLACK)
        NORMALIZE = float(_SOFT_NORMALIZE)

        try:
            results = build_constraint(con_def, ctx)
        except Exception as e:
            logger.warning(f"LP Soft constraint '{cname}' build failed: {e}")
            return None

        if not results:
            return None

        slack_entries = []
        constraint_count = 0

        for idx, (lhs_val, orig_op, rhs_val) in enumerate(results):
            slack_name = f"slack_{cname}_{idx}"

            try:
                slack = solver.NumVar(0, MAX_SLACK, slack_name)

                if orig_op in ("<=", "<"):
                    ct = solver.Constraint(-solver.infinity(), 0, f"soft_{cname}_{idx}")
                    # lhs - rhs - slack <= 0  →  lhs <= rhs + slack
                    # LP에서는 직접 expression을 만들기 어려우므로
                    # build_constraint 결과가 숫자인 경우 처리
                    solver.Add(lhs_val - slack <= rhs_val)
                elif orig_op in (">=", ">"):
                    solver.Add(lhs_val + slack >= rhs_val)
                elif orig_op == "==":
                    solver.Add(lhs_val <= rhs_val + slack)
                    solver.Add(lhs_val >= rhs_val - slack)
                else:
                    continue

                alpha = weight / NORMALIZE
                slack_entries.append((slack, alpha))
                constraint_count += 1

            except Exception as e:
                logger.warning(f"LP Soft constraint '{cname}' idx={idx} failed: {e}")
                continue

        if constraint_count > 0:
            return (constraint_count, slack_entries)
        return None

    # ★ NEW: LP 목적함수 + soft penalty
    def _parse_objective_lp_with_soft(self, solver, var_map, obj_def, bound_data, soft_slack_vars) -> bool:
        """LP 목적함수 = 원래 objective + Σ(alpha * slack)"""
        obj_type = obj_def.get("type", "minimize")
        objective = solver.Objective()

        for vid, v in var_map.items():
            if isinstance(v, dict):
                for var in v.values():
                    objective.SetCoefficient(var, 1)
            else:
                objective.SetCoefficient(v, 1)

        # ★ soft penalty 추가
        for slack, alpha in soft_slack_vars:
            objective.SetCoefficient(slack, alpha)

        if obj_type == "minimize":
            objective.SetMinimization()
        else:
            objective.SetMaximization()

        if soft_slack_vars:
            logger.info(f"LP Objective: {obj_type} + {len(soft_slack_vars)} soft penalties")

        return True

    # ========== struct_builder 기반 목적함수 (기존 - 내부 호출용 유지) ==========

    def _parse_objective_cpsat_struct(self, model, var_map, obj_def, ctx) -> bool:
        """구조화된 목적함수 처리"""
        from engine.compiler.struct_builder import build_objective
        obj_type, obj_val = build_objective(obj_def, ctx)

        if obj_val is not None:
            try:
                if obj_type == "minimize":
                    model.minimize(obj_val)
                else:
                    model.maximize(obj_val)
                logger.info(f"Objective set: {obj_type} (structured/expression)")
                return True
            except Exception as e:
                logger.warning(f"Structured objective failed: {e}")
                return False
        return False

    # ========== Legacy Fallback (기존 정규식 기반) ==========

    def _parse_constraint_cpsat_legacy(self, model, var_map, con_def, bound_data) -> int:
        """CP-SAT 제약 파싱 - 기존 패턴 매칭 (fallback)"""
        expr = con_def.get("expression", "").strip()
        for_each = con_def.get("for_each", "")
        count = 0

        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for group_key, group_vars in groups.items():
                            model.add(sum(group_vars) == 1)
                            count += 1
                        break

        elif "sum" in expr and ("<=" in expr or ">=" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[-1], []).append(var)
                        nums = re.findall(r'<=\s*(\d+)', expr)
                        ub = int(nums[0]) if nums else 10
                        for group_key, group_vars in groups.items():
                            model.add(sum(group_vars) <= ub)
                            count += 1
                        break

        return count

    def _parse_objective_cpsat_legacy(self, model, var_map, obj_def, bound_data) -> bool:
        """CP-SAT 목적함수 - 기존 로직 (fallback)"""
        obj_type = obj_def.get("type", "minimize")
        all_vars = []
        for vid, v in var_map.items():
            if isinstance(v, dict):
                all_vars.extend(v.values())
            else:
                all_vars.append(v)

        if not all_vars:
            return False

        if obj_type == "minimize":
            model.minimize(sum(all_vars))
        else:
            model.maximize(sum(all_vars))
        return True

    def _parse_constraint_lp_legacy(self, solver, var_map, con_def, bound_data) -> int:
        """LP 제약 - 기존 로직 (fallback)"""
        expr = con_def.get("expression", "").strip()
        count = 0

        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for group_key, group_vars in groups.items():
                            ct = solver.Constraint(1, 1, f"assign_{group_key}")
                            for var in group_vars:
                                ct.SetCoefficient(var, 1)
                            count += 1
                        break

        return count

    def _parse_objective_lp_legacy(self, solver, var_map, obj_def, bound_data) -> bool:
        """LP 목적함수 - 기존 로직 (fallback)"""
        obj_type = obj_def.get("type", "minimize")
        objective = solver.Objective()

        for vid, v in var_map.items():
            if isinstance(v, dict):
                for var in v.values():
                    objective.SetCoefficient(var, 1)
            else:
                objective.SetCoefficient(v, 1)

        if obj_type == "minimize":
            objective.SetMinimization()
        else:
            objective.SetMaximization()
        return True
