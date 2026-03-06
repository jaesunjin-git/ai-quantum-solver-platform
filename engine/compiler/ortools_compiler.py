import json
import logging
import re
from typing import Dict, Any, List, Optional
from .base import BaseCompiler, CompileResult
from .struct_builder import BuildContext, build_constraint, build_constraints_batch, apply_constraint_cpsat, apply_constraint_lp, eval_node
from .expression_parser import parse_and_apply_expression

logger = logging.getLogger(__name__)


# ★ NEW: soft constraint weight 로딩
def _load_soft_weights() -> Dict[str, float]:
    """constraints.yaml에서 soft constraint의 weight 값을 로딩"""
    import os
    try:
        import yaml
    except ImportError:
        return {}
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    domains_dir = os.path.join(base, "knowledge", "domains")
    weights = {}
    if not os.path.isdir(domains_dir):
        return weights
    for dname in os.listdir(domains_dir):
        cpath = os.path.join(domains_dir, dname, "constraints.yaml")
        if not os.path.isfile(cpath):
            continue
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
        except Exception:
            continue
        for cid, cdef in (cdata.get("soft") or {}).items():
            if isinstance(cdef, dict):
                weights[cid] = float(cdef.get("weight", 1.0))
    return weights


class ORToolsCompiler(BaseCompiler):
    """OR-Tools CP-SAT / LP 컴파일러 (struct_builder 기반)"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        try:
            var_types = set()
            for v in math_model.get("variables", []):
                var_types.add(self._get_variable_type(v))

            has_continuous = "continuous" in var_types
            if has_continuous:
                return self._compile_lp(math_model, bound_data, **kwargs)
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

        # 2. 제약조건 - struct_builder 사용 (3단계 fallback)
        param_map = bound_data.get("parameters", {})
        set_map = bound_data.get("sets", {})
        
        # --- overlap_pairs 자동 로딩 ---
        _overlap_path = None
        import os as _os2
        if 'overlap_pairs' not in set_map or len(set_map.get('overlap_pairs', [])) < 2:
            _op_candidates = []
            if _os2.path.exists('uploads'):
                for _d in _os2.listdir('uploads'):
                    _np = _os2.path.join('uploads', _d, 'normalized', 'overlap_pairs.json')
                    if _os2.path.exists(_np):
                        _op_candidates.append(_np)
            for _opc in _op_candidates:
                try:
                    with open(_opc, encoding='utf-8') as _opf:
                        import json as _ojson
                        _op_data = _ojson.load(_opf)
                    if isinstance(_op_data, list) and len(_op_data) > 1:
                        set_map['overlap_pairs'] = [tuple(p) for p in _op_data]
                        logger.info(f"CP-SAT overlap_pairs loaded: {len(set_map['overlap_pairs'])} pairs from {_opc}")
                        break
                except Exception as _ope:
                    logger.warning(f"CP-SAT overlap_pairs load failed: {_ope}")
        # --- end overlap_pairs ---

        ctx = BuildContext(var_map, param_map, set_map)

        logger.info(f"BuildContext - sets: {list(set_map.keys())}, sizes: {[len(v) for v in set_map.values()]}")
        logger.info(f"BuildContext - params: {list(param_map.keys())[:20]}")
        logger.info(f"BuildContext - vars: {list(var_map.keys())}")

        total_constraints = 0
        constraint_defs = math_model.get("constraints", [])

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
            logger.info(f"DEBUG constraint '{cname}': keys={_keys}, has_lhs={_has_lhs}, has_rhs={_has_rhs}, has_expr={_has_expr}")
            if _has_lhs:
                logger.info(f"  lhs={json.dumps(con_def['lhs'], ensure_ascii=False, default=str)[:200]}")
            if _has_rhs:
                logger.info(f"  rhs={json.dumps(con_def['rhs'], ensure_ascii=False, default=str)[:200]}")
            if _has_expr:
                logger.info(f"  expr={con_def['expression'][:150]}")
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
                    logger.info(f"Soft constraint '{cname}': {slack_count} instances applied")
                else:
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
                logger.info(f"Constraint '{cname}': {parsed_count} instances (legacy regex)")
            else:
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
                "soft_constraints_applied": soft_applied_count,       # ★ NEW
                "soft_slack_variables": len(soft_slack_vars),          # ★ NEW
            },
        )

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

        if not has_struct:
            return None

        # weight 결정: constraint 정의 > YAML > 기본값 1.0
        weight = float(con_def.get("weight", soft_weights.get(cname, 1.0)))

        # ★ 스케일 정규화: 주 목적함수(duty 수)와 비교하여 적절한 계수 산출
        #   primary_scale ≈ duty 수, soft 패널티가 전체의 ~10% 수준
        #   MAX_SLACK을 제한하여 solver 성능 보장
        MAX_SLACK = 1440  # 최대 슬랙: 24시간(분)
        NORMALIZE = 300   # 정규화 상수 (5시간 = 300분 기준)

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

        # --- LP: overlap_pairs 자동 로딩 ---
        if 'overlap_pairs' not in set_map or len(set_map.get('overlap_pairs', [])) < 2:
            import os as _os2
            import json as _ojson2
            _op_candidates = []
            if hasattr(self, '_project_id'):
                _op_candidates.append(f'uploads/{self._project_id}/normalized/overlap_pairs.json')
            # bound_data에서 project_id 추출 시도
            for _set_def in math_model.get('sets', []):
                _sf = _set_def.get('source_file', '')
                if _sf:
                    _base = _os2.path.dirname(_os2.path.join(str(getattr(self, '_upload_dir', 'uploads')), _sf))
                    _op_candidates.append(_os2.path.join(_base, 'overlap_pairs.json'))
            # uploads/*/normalized/ 패턴으로 탐색
            if _os2.path.exists('uploads'):
                for _d in _os2.listdir('uploads'):
                    _np = _os2.path.join('uploads', _d, 'normalized', 'overlap_pairs.json')
                    if _os2.path.exists(_np):
                        _op_candidates.append(_np)
            for _opc in _op_candidates:
                if _os2.path.exists(_opc):
                    try:
                        with open(_opc, encoding='utf-8') as _opf:
                            _op_data = _ojson2.load(_opf)
                        if isinstance(_op_data, list) and len(_op_data) > 1:
                            set_map['overlap_pairs'] = [tuple(p) for p in _op_data]
                            logger.info(f"LP overlap_pairs loaded: {len(set_map['overlap_pairs'])} pairs from {_opc}")
                            break
                    except Exception as _ope:
                        logger.warning(f"LP overlap_pairs load failed: {_ope}")
        # --- end LP overlap_pairs ---

        ctx = BuildContext(var_map, param_map, set_map)

        logger.info(f"BuildContext - sets: {list(set_map.keys())}, sizes: {[len(v) for v in set_map.values()]}")
        logger.info(f"BuildContext - params: {list(param_map.keys())[:20]}")
        logger.info(f"BuildContext - vars: {list(var_map.keys())}")

        # === DEBUG DUMP (임시) ===
        try:
            import json as _djson
            _debug = {
                "param_keys": list(param_map.keys()),
                "param_sample": {k: str(v)[:100] for k, v in list(param_map.items())[:30]},
                "set_keys": list(set_map.keys()),
                "set_sizes": {k: len(v) if isinstance(v, (list, tuple)) else str(v) for k, v in set_map.items()},
                "var_keys": list(var_map.keys()),
                "var_types": {k: ("dict" if isinstance(v, dict) else type(v).__name__) for k, v in var_map.items()},
                "var_sizes": {k: len(v) if isinstance(v, dict) else 1 for k, v in var_map.items()},
            }
            # trip_dep_time, trip_arr_time 상세
            for pname in ["trip_dep_time", "trip_arr_time", "trip_duration", "big_m", "max_driving_minutes", "preparation_minutes"]:
                pval = param_map.get(pname)
                if pval is not None:
                    if isinstance(pval, (list, tuple)):
                        _debug[f"param_{pname}"] = f"array len={len(pval)}, first3={list(pval[:3])}"
                    elif isinstance(pval, dict):
                        _debug[f"param_{pname}"] = f"dict len={len(pval)}, keys={list(pval.keys())[:5]}"
                    else:
                        _debug[f"param_{pname}"] = str(pval)
                else:
                    _debug[f"param_{pname}"] = "NOT FOUND"
            
            # overlap_pairs 상세
            op = set_map.get("overlap_pairs", [])
            _debug["overlap_pairs_size"] = len(op) if isinstance(op, (list, tuple)) else str(op)
            if isinstance(op, (list, tuple)) and len(op) > 0:
                _debug["overlap_pairs_sample"] = [str(x) for x in op[:3]]
            
            with open("uploads/94/debug_bound_data.json", "w", encoding="utf-8") as _df:
                _djson.dump(_debug, _df, ensure_ascii=False, indent=2, default=str)
            logger.info("DEBUG: bound_data dumped to uploads/94/debug_bound_data.json")
        except Exception as _de:
            logger.warning(f"DEBUG dump failed: {_de}")
        # === END DEBUG ===


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
        MAX_SLACK = 1440.0
        NORMALIZE = 300.0

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
