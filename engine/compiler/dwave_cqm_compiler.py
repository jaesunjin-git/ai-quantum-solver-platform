# engine/compiler/dwave_cqm_compiler.py
# ============================================================
# D-Wave CQM Compiler v2.0
# struct_builder 연동 - OR-Tools와 동일한 구조화된 제약 처리
# ============================================================

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from .base import BaseCompiler, CompileResult, DataBinder

logger = logging.getLogger(__name__)


CQM_MAX_CONSTRAINTS = 95_000    # D-Wave Hybrid CQM 한도(100K)의 95%
NO_OVERLAP_RESERVE = 5_000      # no_overlap 이후 제약을 위한 최소 예약 예산


class DWaveCQMCompiler(BaseCompiler):
    """수학 모델 IR을 D-Wave CQM으로 변환 (struct_builder 활용)"""

    def compile(self, math_model: Dict, bound_data: Any, **kwargs) -> CompileResult:
        try:
            import dimod

            cqm_budget = int(kwargs.get("cqm_max_constraints", CQM_MAX_CONSTRAINTS))
            cqm = dimod.ConstrainedQuadraticModel()
            var_map: Dict[str, Any] = {}
            total_vars = 0
            total_constraints = 0
            warnings = []

            # ── bound_data에서 세트/파라미터 추출 ──
            # bound_data = {"sets": {I: [...], ...}, "parameters": {name: val, ...}, "set_sizes": {...}}
            if isinstance(bound_data, dict):
                set_map = bound_data.get("sets", {})
                param_map = bound_data.get("parameters", {})
            else:
                set_map = getattr(bound_data, "set_map", {})
                param_map = getattr(bound_data, "param_map", {})

            # ── 0. J 크기 교정 (변수 생성 전 — 불필요한 변수 생성 방지) ──
            _J_vals = set_map.get("J", [])
            if len(_J_vals) > 100:
                _trip_count = len(set_map.get("I", []))
                if _trip_count > 0:
                    _new_j = max(_trip_count // 6, 20)
                    if _new_j < len(_J_vals):
                        set_map["J"] = list(range(1, _new_j + 1))
                        logger.info(f"J size auto-corrected (pre-var): {len(_J_vals)} -> {_new_j} (trips={_trip_count})")

            # ── 1. 변수 생성 ──
            for var_def in math_model.get("variables", []):
                vid = var_def.get("id", "")
                vtype = var_def.get("type", "binary").lower()
                indices = var_def.get("indices", [])

                if not indices:
                    var_map[vid] = self._create_var(dimod, vid, vtype, var_def)
                    total_vars += 1
                else:
                    combos = self._get_index_combos(indices, set_map, math_model)
                    var_map[vid] = {}
                    for combo in combos:
                        key = tuple(combo)
                        name = f"{vid}_{'_'.join(str(c) for c in combo)}"
                        var_map[vid][key] = self._create_var(dimod, name, vtype, var_def)
                        total_vars += 1

            logger.info(f"CQM: created {total_vars} variables")

            # ── 2. overlap_pairs 로딩 (OR-Tools와 동일 방식) ──
            import os as _os
            import json as _ojson
            _project_id = kwargs.get('project_id', '')
            if _project_id and ('overlap_pairs' not in set_map or len(set_map.get('overlap_pairs', [])) < 2):
                _op_path = _os.path.join('uploads', str(_project_id), 'normalized', 'overlap_pairs.json')
                if _os.path.exists(_op_path):
                    try:
                        with open(_op_path, encoding='utf-8') as _opf:
                            _op_data = _ojson.load(_opf)
                        if isinstance(_op_data, list) and len(_op_data) > 0:
                            set_map['overlap_pairs'] = [tuple(p) for p in _op_data]
                            logger.info(f"CQM overlap_pairs loaded: {len(set_map['overlap_pairs'])} pairs from {_op_path}")
                    except Exception as _ope:
                        logger.warning(f"CQM overlap_pairs load failed: {_ope}")
                else:
                    logger.warning(f"CQM overlap_pairs.json not found at {_op_path}")

            # ── 2b. overlap_pairs 중요도 정렬 (겹침 시간 길수록 먼저) ──
            if 'overlap_pairs' in set_map and len(set_map['overlap_pairs']) > 1:
                dep = param_map.get("trip_dep_time")
                arr = param_map.get("trip_arr_time")
                if isinstance(dep, dict) and isinstance(arr, dict):
                    set_map['overlap_pairs'] = self._sort_overlap_pairs_by_duration(
                        set_map['overlap_pairs'], param_map, set_map
                    )
                else:
                    logger.info("overlap_pairs sort skipped: trip params not dict-indexed yet")

            # J 크기 교정은 변수 생성 전(step 0)으로 이동됨

            # ── 3. BuildContext 구성 (struct_builder 공유) ──
            from engine.compiler.struct_builder import BuildContext

            ctx = BuildContext(
                var_map=var_map,
                param_map=param_map,
                set_map=set_map,
                model=cqm,
            )

            # ── 4. 제약조건 (struct_builder 활용) ──
            from engine.compiler.struct_builder import build_constraint

            # ── 제약 우선순위 정렬 ──
            # hard 우선 (priority 내림차순), soft는 weight 내림차순
            # model.json의 'priority' 필드 활용; 없으면 50 기본값
            raw_constraints = math_model.get("constraints", [])
            def _safe_priority(c):
                v = c.get("priority", 50)
                if isinstance(v, (int, float)):
                    return v
                # 문자열 우선순위를 숫자로 변환
                _map = {"critical": 100, "high": 80, "medium": 50, "low": 20}
                return _map.get(str(v).lower(), 50)

            def _safe_weight(c):
                v = c.get("weight", 0)
                try:
                    return float(v) if v else 0.0
                except (ValueError, TypeError):
                    return 0.0

            hard_cons = sorted(
                [(i, c) for i, c in enumerate(raw_constraints) if c.get("category", "hard") == "hard"],
                key=lambda x: (-_safe_priority(x[1]), x[0])
            )
            soft_cons = sorted(
                [(i, c) for i, c in enumerate(raw_constraints) if c.get("category", "hard") != "hard"],
                key=lambda x: (-_safe_weight(x[1]), x[0])
            )
            sorted_constraints = [c for _, c in hard_cons] + [c for _, c in soft_cons]

            # 정렬 결과 로깅
            logger.info("CQM constraint processing order:")
            for c in sorted_constraints:
                cname = c.get("name", "?")
                cat = c.get("category", "hard")
                pri = c.get("priority", "-")
                w = c.get("weight", "-")
                logger.info(f"  [{cat}] {cname} (priority={pri}, weight={w})")

            num_constraints = len(sorted_constraints)
            for con_idx, con_def in enumerate(sorted_constraints):
                cid = con_def.get("id") or con_def.get("name", "unknown")
                category = con_def.get("category", "hard")
                weight = con_def.get("weight")
                op = con_def.get("operator", "==")

                has_lhs = con_def.get("lhs") is not None
                has_rhs = con_def.get("rhs") is not None

                # ── 예산 초과 시 스킵 ──
                remaining = cqm_budget - total_constraints
                if remaining <= 0:
                    warnings.append(f"Constraint {cid}: skipped (budget exhausted {total_constraints}/{cqm_budget})")
                    logger.warning(f"CQM budget exhausted at {total_constraints}, skipping '{cid}'")
                    continue

                # ── Per-constraint budget cap ──
                # 단일 제약이 전체 예산의 40%를 초과할 수 없음 (나머지 제약 보호)
                # 남은 제약이 1~2개면 제한 해제 (마지막에는 자유롭게 사용)
                constraints_left = max(1, num_constraints - con_idx)
                if constraints_left <= 2:
                    per_constraint_cap = remaining
                else:
                    per_constraint_cap = max(500, int(cqm_budget * 0.4))
                explicit_max = con_def.get("max_instances")

                if has_lhs and has_rhs:
                    # ── compact activation linking 감지 및 변환 ──
                    # y[j] >= x[i,j] for i in I, j in J  →  sum_i x[i,j] <= |I| * y[j] for j in J
                    compact_count = self._try_compact_activation_linking(cqm, var_map, con_def, set_map)
                    if compact_count > 0:
                        total_constraints += compact_count
                        logger.info(f"Constraint '{cid}': {compact_count} instances (compact activation)")
                        continue

                    # overlap_pairs 패턴 감지 → 고속 직접 처리 (build_constraint 우회)
                    # no_overlap 이후 제약들을 위해 NO_OVERLAP_RESERVE만큼 예약
                    no_overlap_budget = max(0, remaining - NO_OVERLAP_RESERVE)
                    fast_count = self._try_fast_no_overlap(cqm, var_map, con_def, set_map, no_overlap_budget)
                    if fast_count > 0:
                        total_constraints += fast_count
                        logger.info(f"Constraint '{cid}': {fast_count} instances (no_overlap fast-path)")
                        continue

                    # 구조화된 제약 -> build_constraint로 dimod 표현식 생성
                    con_max = explicit_max
                    # for_each가 두 집합 이상 (예: i in I, j in J)이면 max_instances 미설정 시
                    # budget 보호를 위해 1000 기본 적용 (J auto-correction 후 재검토 필요)
                    if not con_max:
                        _fe = con_def.get("for_each", "")
                        if _fe.count(" in ") >= 2:
                            con_max = 1000
                    # per-constraint cap 적용: 단일 제약이 전체 예산의 40%를 초과할 수 없음
                    effective_max = min(remaining, per_constraint_cap, con_max) if con_max else min(remaining, per_constraint_cap)
                    try:
                        _missing_before = set(ctx.missing_params) if hasattr(ctx, 'missing_params') else set()
                        tuples = build_constraint(con_def, ctx, max_instances=effective_max)
                        _missing_after = getattr(ctx, 'missing_params', set()) - _missing_before
                        # 안전망: per_constraint_cap 초과분 잘라내기
                        if len(tuples) > per_constraint_cap:
                            logger.warning(
                                f"Constraint '{cid}': {len(tuples)} instances capped to {per_constraint_cap} (per-constraint cap)"
                            )
                            warnings.append(
                                f"Constraint {cid}: capped {len(tuples)}→{per_constraint_cap} (fair-share budget)"
                            )
                            tuples = tuples[:per_constraint_cap]
                        added = 0
                        _first_failure_logged = False
                        _tautology_count = 0
                        _infeasible_count = 0
                        for idx, (lhs_val, op_str, rhs_val) in enumerate(tuples):
                            label = f"{cid}_{idx}"
                            # 진단 로그: 첫 3개 인스턴스
                            if idx < 3:
                                logger.debug(
                                    "constraint=%s idx=%d lhs_type=%s rhs_type=%s lhs=%s rhs=%s",
                                    cid, idx, type(lhs_val).__name__, type(rhs_val).__name__,
                                    str(lhs_val)[:80], str(rhs_val)[:80]
                                )
                            try:
                                result = self._add_cqm_constraint(
                                    cqm, lhs_val, op_str, rhs_val,
                                    label, category, weight
                                )
                                if result == "ok":
                                    added += 1
                                elif result == "tautology":
                                    _tautology_count += 1
                                elif result == "infeasible":
                                    _infeasible_count += 1
                                    if category == "hard" and _infeasible_count <= 3:
                                        logger.error(
                                            f"Constant infeasible (hard): {label} | "
                                            f"expr={con_def.get('expression_template', '')[:80]} | "
                                            f"lhs={lhs_val} {op_str} rhs={rhs_val}"
                                        )
                                        warnings.append(f"Constraint {cid}_{idx}: constant infeasible ({lhs_val} {op_str} {rhs_val})")
                            except Exception as e:
                                if (added == 0 and idx < 3) or not _first_failure_logged:
                                    logger.warning(
                                        f"CQM constraint {label} failed: {e} "
                                        f"(lhs_type={type(lhs_val).__name__}, rhs_type={type(rhs_val).__name__})"
                                    )
                                    _first_failure_logged = True
                        if _infeasible_count > 0:
                            logger.warning(f"Constraint '{cid}': {_infeasible_count} constant infeasible instances (category={category})")
                        if _tautology_count > 0:
                            logger.debug(f"Constraint '{cid}': {_tautology_count} tautology instances skipped")

                        if added > 0:
                            total_constraints += added
                            logger.info(f"Constraint '{cid}': {added} instances (structured, budget={per_constraint_cap})")
                        else:
                            # 누락 파라미터가 있으면 expr fallback도 동일하게 실패할 것이므로 스킵
                            if _missing_after:
                                _mp = ", ".join(sorted(_missing_after))
                                warnings.append(f"Constraint {cid}: skipped (missing params: {_mp})")
                                logger.warning(f"Constraint '{cid}': skipped — missing params: {_mp}")
                            elif con_def.get("expression"):
                                # 구조화 경로 실패 시 expression_parser로 fallback
                                remaining2 = min(cqm_budget - total_constraints, per_constraint_cap)
                                count = self._parse_constraint_expr_cqm(
                                    cqm, var_map, con_def, ctx, max_count=remaining2
                                )
                                if count > 0:
                                    total_constraints += count
                                    logger.info(f"Constraint '{cid}': {count} instances (structured→expr fallback, budget={per_constraint_cap})")
                                else:
                                    warnings.append(f"Constraint {cid}: 0 instances (structured+expr both failed)")
                            else:
                                warnings.append(f"Constraint {cid}: 0 instances from structured parse")

                    except Exception as e:
                        from engine.compiler.errors import StructuredFallbackAllowed, StructuredDataError
                        if isinstance(e, StructuredFallbackAllowed):
                            # 패턴 미지원 → expression_parser fallback 허용
                            logger.info(f"Constraint '{cid}': unsupported structured pattern → expr fallback ({e})")
                            if con_def.get("expression") or con_def.get("expression_template"):
                                remaining2 = min(cqm_budget - total_constraints, per_constraint_cap)
                                count = self._parse_constraint_expr_cqm(
                                    cqm, var_map, con_def, ctx, max_count=remaining2
                                )
                                if count > 0:
                                    total_constraints += count
                                    logger.info(f"Constraint '{cid}': {count} instances (fallback→expr)")
                                else:
                                    warnings.append(f"Constraint {cid}: fallback expr also failed")
                            else:
                                warnings.append(f"Constraint {cid}: unsupported pattern, no expression fallback")
                        elif isinstance(e, StructuredDataError):
                            # 데이터 오류 → fallback 금지
                            logger.error(f"Constraint '{cid}' DATA ERROR: {e}")
                            warnings.append(f"Constraint {cid}: DATA ERROR — {e}")
                        else:
                            logger.warning(f"Constraint '{cid}' structured parse failed: {e}")
                            warnings.append(f"Constraint {cid}: structured parse error")
                else:
                    # ── Affine Collector 경로 시도 (expression_parser보다 ~100x 빠름) ──
                    affine_count = self._try_affine_collector(
                        cqm, var_map, con_def, ctx, max_count=min(remaining, per_constraint_cap)
                    )
                    if affine_count > 0:
                        total_constraints += affine_count
                        logger.info(f"Constraint '{cid}': {affine_count} instances (affine_collector, budget={per_constraint_cap})")
                        continue

                    # Affine 미지원 → expression_parser 경유 CQM 적용 시도
                    expr_budget = min(remaining, per_constraint_cap)
                    count = self._parse_constraint_expr_cqm(cqm, var_map, con_def, ctx, max_count=expr_budget)
                    if count > 0:
                        total_constraints += count
                        logger.info(f"Constraint '{cid}': {count} instances (expression_parser→cqm, budget={per_constraint_cap})")
                    else:
                        # 레거시 expression 파싱 (폴백)
                        count = self._parse_constraint_legacy(cqm, var_map, con_def, set_map, param_map)
                        if count > 0:
                            total_constraints += count
                            logger.info(f"Constraint '{cid}': {count} instances (legacy)")
                        else:
                            warnings.append(f"Constraint {cid}: could not parse")

            logger.info(f"CQM: created {total_constraints} constraints")

            # ── 4. 목적함수 ──
            obj = math_model.get("objective", {})
            obj_parsed = self._parse_objective(cqm, var_map, obj, ctx)
            if not obj_parsed:
                warnings.append("Objective: could not parse, using default minimize sum")
                self._set_default_objective(cqm, var_map)

            return CompileResult(
                success=True,
                solver_model=cqm,
                solver_type="cqm",
                variable_count=total_vars,
                constraint_count=total_constraints,
                variable_map=var_map,
                warnings=warnings,
                metadata={"model_type": "CQM", "engine": "D-Wave"},
            )

        except ImportError as e:
            return CompileResult(
                success=False,
                error=f"dimod package not installed: {e}. Run: pip install dwave-ocean-sdk"
            )
        except Exception as e:
            logger.error(f"CQM compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    # ── 변수 생성 ──

    def _create_var(self, dimod, name: str, vtype: str, var_def: Dict):
        if vtype == "binary":
            return dimod.Binary(name)
        elif vtype == "integer":
            lb = int(var_def.get("lower_bound") or 0)
            ub = int(var_def.get("upper_bound") or 1000000)
            return dimod.Integer(name, lower_bound=lb, upper_bound=ub)
        else:
            lb = float(var_def.get("lower_bound") or 0)
            ub = float(var_def.get("upper_bound") or 1e7)
            return dimod.Real(name, lower_bound=lb, upper_bound=ub)

    def _get_index_combos(self, indices: List[str], set_map: Dict, math_model: Dict) -> List[List]:
        """인덱스 조합 계산"""
        sets_in_order = []
        for idx_name in indices:
            vals = set_map.get(idx_name, [])
            if not vals:
                # 모델 정의에서 set 크기 조회
                for s_def in math_model.get("sets", []):
                    if s_def.get("id") == idx_name:
                        size = s_def.get("size", 0)
                        if size > 0:
                            vals = list(range(1, size + 1))
                        break
            sets_in_order.append(vals)

        if not sets_in_order or any(len(s) == 0 for s in sets_in_order):
            return []

        # 카르테시안 프로덕트
        from itertools import product
        return [list(combo) for combo in product(*sets_in_order)]

    # ── CQM 제약 추가 ──

    def _add_cqm_constraint(self, cqm, lhs, op: str, rhs, label: str, category: str, weight=None):
        """
        dimod 표현식으로 CQM 제약 추가.

        Returns: "ok" | "tautology" | "infeasible"
        """
        is_soft = (category == "soft") and weight
        EPS = 1e-9

        # constant-only 검사: lhs와 rhs가 둘 다 scalar이면 변수 없는 제약
        if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
            diff_val = float(lhs - rhs)
            is_tautology = (
                (op in ("<=", "le") and diff_val <= EPS) or
                (op in (">=", "ge") and diff_val >= -EPS) or
                (op in ("==", "eq", "=") and abs(diff_val) < EPS)
            )
            if is_tautology:
                return "tautology"
            # 항상 거짓
            return "infeasible"

        try:
            diff = lhs - rhs
        except TypeError:
            # 타입 불일치 시 숫자를 명시적으로 처리
            if isinstance(rhs, (int, float)):
                diff = lhs - rhs
            elif isinstance(lhs, (int, float)):
                diff = lhs - rhs
            else:
                from engine.compiler.errors import NonScalarBoundValueError
                raise NonScalarBoundValueError(
                    f"lhs_type={type(lhs).__name__}, rhs_type={type(rhs).__name__}"
                )

        if op in ("<=", "le"):
            constraint_expr = diff <= 0
        elif op in (">=", "ge"):
            constraint_expr = diff >= 0
        elif op in ("==", "eq", "="):
            constraint_expr = diff == 0
        else:
            constraint_expr = diff <= 0

        if is_soft:
            cqm.add_constraint(constraint_expr, label=label, weight=float(weight))
        else:
            cqm.add_constraint(constraint_expr, label=label)
        return "ok"

    # ── 고속 경로: overlap pairs (time_compatibility) ──

    def _fast_add_overlap_constraints(self, cqm, var_map, con_def, set_map, param_map=None, max_constraints=56000) -> int:
        """
        y[i,d] + y[j,d] <= 1 패턴을 eval_node 없이 직접 생성.
        D-Wave CQM 한도(100K)를 초과하지 않도록 겹침 강도 기반 필터링 적용.
        """
        pairs = con_def.get("_overlap_pairs", [])
        if not pairs:
            return 0

        y_vars = var_map.get("y", {})
        if not isinstance(y_vars, dict) or not y_vars:
            return 0

        D_vals = set_map.get("D", [])
        if not D_vals:
            return 0

        cid = con_def.get("id") or con_def.get("name", "overlap")

        # 제약 수가 한도를 초과하면 겹침 강도 기반 필터링
        total_possible = len(pairs) * len(D_vals)
        if total_possible > max_constraints and param_map:
            dep_times = param_map.get("trip_dep_time", [])
            arr_times = param_map.get("trip_arr_time", [])
            I_vals = set_map.get("I", [])

            if dep_times and arr_times and I_vals:
                id_to_idx = {v: i for i, v in enumerate(I_vals)}
                scored_pairs = []
                for pair in pairs:
                    i_id = int(pair[0]) if isinstance(pair[0], str) else pair[0]
                    j_id = int(pair[1]) if isinstance(pair[1], str) else pair[1]
                    i_idx = id_to_idx.get(i_id)
                    j_idx = id_to_idx.get(j_id)
                    if i_idx is not None and j_idx is not None:
                        overlap = min(arr_times[i_idx], arr_times[j_idx]) - max(dep_times[i_idx], dep_times[j_idx])
                        scored_pairs.append((overlap, pair))

                scored_pairs.sort(key=lambda x: x[0], reverse=True)
                max_pairs = max_constraints // len(D_vals)
                filtered_pairs = [p for _, p in scored_pairs[:max_pairs]]
                min_overlap = scored_pairs[min(max_pairs-1, len(scored_pairs)-1)][0] if scored_pairs else 0

                logger.info(
                    f"CQM overlap filter: {len(pairs)} -> {len(filtered_pairs)} pairs "
                    f"(min_overlap={min_overlap:.0f}min, limit={max_constraints})"
                )
                pairs = filtered_pairs

        count = 0
        for pi, pair in enumerate(pairs):
            i_val = int(pair[0]) if isinstance(pair[0], str) else pair[0]
            j_val = int(pair[1]) if isinstance(pair[1], str) else pair[1]

            for d_val in D_vals:
                d_int = int(d_val) if isinstance(d_val, str) else d_val
                yi = y_vars.get((i_val, d_int))
                yj = y_vars.get((j_val, d_int))

                if yi is not None and yj is not None:
                    cqm.add_constraint(yi + yj <= 1, label=f"{cid}_{count}")
                    count += 1

        logger.info(f"CQM fast-path: {len(pairs)} pairs x {len(D_vals)} D = {count} constraints")
        return count

    # ── 목적함수 ──

    def _parse_objective(self, cqm, var_map: Dict, obj_def: Dict, ctx) -> bool:
        """구조화된 목적함수 파싱"""
        from engine.compiler.struct_builder import build_objective

        obj_type, obj_val = build_objective(obj_def, ctx)

        if obj_val is not None:
            try:
                if obj_type == "minimize":
                    cqm.set_objective(obj_val)
                else:
                    cqm.set_objective(-obj_val)
                logger.info(f"CQM Objective set: {obj_type} (structured)")
                return True
            except Exception as e:
                logger.warning(f"CQM structured objective failed: {e}")

        # 폴백: expression 파싱
        expr_str = obj_def.get("expression", "")
        if expr_str:
            return self._parse_objective_from_expr(cqm, var_map, obj_def, ctx)

        return False

    def _parse_objective_from_expr(self, cqm, var_map, obj_def, ctx) -> bool:
        """expression 문자열에서 목적함수 파싱"""
        expr_str = obj_def.get("expression", "")
        obj_type = obj_def.get("type", "minimize")

        # sum(u[d] for d in D) 패턴
        m = re.match(r'sum\((\w+)\[(\w+)\]\s+for\s+(\w+)\s+in\s+(\w+)\)', expr_str)
        if m:
            var_name, idx_var, loop_var, set_name = m.groups()
            v = var_map.get(var_name)
            if isinstance(v, dict) and v:
                total = sum(v.values())
                if obj_type == "maximize":
                    total = -total
                cqm.set_objective(total)
                logger.info(f"CQM Objective from expression: {expr_str}")
                return True

        return False

    def _set_default_objective(self, cqm, var_map):
        """기본 목적함수: 모든 변수 합 최소화"""
        all_vars = []
        for v in var_map.values():
            if isinstance(v, dict):
                all_vars.extend(v.values())
            else:
                all_vars.append(v)
        if all_vars:
            cqm.set_objective(sum(all_vars))

    # ── Compact Activation Linking ──

    def _try_compact_activation_linking(self, cqm, var_map, con_def, set_map) -> int:
        """
        y[j] >= x[i,j]  (for each i in I, j in J)  패턴을 감지하여
        sum_i x[i,j] <= |I| * y[j]  (for each j in J)  compact form으로 변환.

        25,600개 → 160개로 압축 (|I|×|J| → |J|).

        감지 조건:
          - lhs: {"type":"variable", "indices": [idx1]}         (단일 인덱스)
          - rhs: {"type":"variable", "indices": [idx2, idx1]}   (두 인덱스, 마지막이 lhs와 공유)
          - operator: ">="
        """
        lhs_node = con_def.get("lhs", {})
        rhs_node = con_def.get("rhs", {})
        op = con_def.get("operator", "")

        if not (isinstance(lhs_node, dict) and isinstance(rhs_node, dict)):
            return 0

        # ── 패턴 감지: x[i,d] <= u[d] 또는 u[d] >= x[i,d] ──
        # 두 형식 모두 지원: {"type":"variable"} 및 {"var":{"name":...}}
        def _extract_var_info(node):
            """노드에서 (var_name, indices_list) 추출. 실패 시 (None, [])"""
            if node.get("type") == "variable":
                return node.get("id", ""), node.get("indices", [])
            if "var" in node and isinstance(node["var"], dict):
                vn = node["var"].get("name", node["var"].get("id", ""))
                idx_raw = node["var"].get("index", "")
                if isinstance(idx_raw, str):
                    idx_list = [s.strip() for s in idx_raw.strip("[]").split(",") if s.strip()]
                else:
                    idx_list = list(idx_raw) if idx_raw else []
                return vn, idx_list
            return None, []

        lhs_name, lhs_indices = _extract_var_info(lhs_node)
        rhs_name, rhs_indices = _extract_var_info(rhs_node)
        if lhs_name is None or rhs_name is None:
            return 0

        # x[i,d] <= u[d] → assign=lhs(2 idx), act=rhs(1 idx)
        # u[d] >= x[i,d] → act=lhs(1 idx), assign=rhs(2 idx)
        if op == "<=" and len(lhs_indices) == 2 and len(rhs_indices) == 1:
            assign_var_name, assign_indices = lhs_name, lhs_indices
            act_var_name, act_indices = rhs_name, rhs_indices
        elif op == ">=" and len(lhs_indices) == 1 and len(rhs_indices) == 2:
            act_var_name, act_indices = lhs_name, lhs_indices
            assign_var_name, assign_indices = rhs_name, rhs_indices
        else:
            return 0

        # 마지막 인덱스가 공유되어야 함
        shared_idx = act_indices[0]
        if assign_indices[-1] != shared_idx:
            return 0

        inner_idx = assign_indices[0]  # i (sum over)
        outer_idx = shared_idx          # d (outer loop)

        # 대문자 규칙으로 set 이름 추론: "i" → "I", "j" → "J"
        inner_set = set_map.get(inner_idx.upper(), [])
        outer_set = set_map.get(outer_idx.upper(), [])

        act_vars = var_map.get(act_var_name, {})
        assign_vars = var_map.get(assign_var_name, {})

        if not inner_set or not outer_set:
            return 0
        if not isinstance(act_vars, dict) or not isinstance(assign_vars, dict):
            return 0

        n_inner = len(inner_set)
        cid = con_def.get("id") or con_def.get("name", "unknown")
        count = 0

        for j_val in outer_set:
            # y[j] 변수 조회
            y_var = self._lookup_var(act_vars, (j_val,))
            if y_var is None:
                continue

            # sum_{i in I} x[i,j]
            x_terms = []
            for i_val in inner_set:
                x_var = self._lookup_var(assign_vars, (i_val, j_val))
                if x_var is not None:
                    x_terms.append(x_var)

            if not x_terms:
                continue

            x_sum = x_terms[0]
            for t in x_terms[1:]:
                x_sum = x_sum + t

            # sum_i x[i,j] <= |I| * y[j]
            label = f"{cid}_compact_{j_val}"
            try:
                cqm.add_constraint(x_sum - n_inner * y_var <= 0, label=label)
                count += 1
            except Exception as e:
                logger.warning(f"Compact activation constraint {label} failed: {e}")

        if count > 0:
            logger.info(
                f"Compact activation '{cid}': {count} constraints "
                f"(was {len(inner_set) * len(outer_set)}, saved {len(inner_set) * len(outer_set) - count})"
            )
        return count

    # 반복 에러 로그 제한용
    _affine_error_logged: set = set()

    def _try_affine_collector(self, cqm, var_map, con_def, ctx, max_count: int = 0) -> int:
        """
        Affine Collector 경로: expression_template → AST → AffineExprIR → dimod CQM.
        expression_parser의 51,200회 문자열 파싱을 우회하여 ~100x 속도 향상.

        Returns: 추가된 제약 수 (0이면 미지원 → expression_parser fallback)
        """
        from engine.compiler.affine_collector import (
            parse_constraint_expr_cached, collect_affine, normalize_constraint,
            check_constant_constraint, lower_affine_to_dimod, build_var_lookup,
        )
        from engine.compiler.errors import StructuredFallbackAllowed, StructuredDataError
        from engine.compiler.struct_builder import normalize_index_key

        expr_str = (con_def.get("expression") or con_def.get("expression_template") or "").strip()
        if not expr_str:
            return 0

        # 1. AST 파싱 + affine 지원 여부 캐시
        entry = parse_constraint_expr_cached(expr_str)
        if entry is None or not entry.affine_supported:
            return 0

        cid = con_def.get("name", con_def.get("id", "?"))
        category = con_def.get("category", "hard")
        weight = con_def.get("penalty_weight") or con_def.get("weight")
        for_each = con_def.get("for_each", "")

        # 2. VarRef → dimod variable 매핑 (한 번만 생성)
        if not hasattr(self, '_affine_var_lookup') or self._affine_var_lookup_id != id(var_map):
            self._affine_var_lookup = build_var_lookup(var_map)
            self._affine_var_lookup_id = id(var_map)

        # 3. 바인딩 생성
        from engine.compiler.expression_parser import _parse_for_each
        bindings = _parse_for_each(for_each, ctx)
        if max_count > 0 and len(bindings) > max_count:
            bindings = bindings[:max_count]

        # 4. 바인딩별 affine 수집 + lowering
        count = 0
        for idx, binding in enumerate(bindings):
            try:
                lhs_ir = collect_affine(entry.ast_lhs, binding, ctx)
                rhs_ir = collect_affine(entry.ast_rhs, binding, ctx)
                diff, op, _ = normalize_constraint(lhs_ir, entry.op, rhs_ir)

                # constant-only 검사
                cc = check_constant_constraint(diff, op)
                if cc == "tautology":
                    continue
                elif cc == "infeasible":
                    logger.warning(f"Constraint {cid}_{idx}: constant infeasible ({diff.constant} {op} 0)")
                    continue

                # dimod lowering
                dimod_expr = lower_affine_to_dimod(diff, self._affine_var_lookup)
                label = f"{cid}_{idx}"

                is_soft = category == "soft" and weight
                if op == "<=":
                    constraint_expr = dimod_expr <= 0
                elif op == ">=":
                    constraint_expr = dimod_expr >= 0
                elif op == "==":
                    constraint_expr = dimod_expr == 0
                else:
                    constraint_expr = dimod_expr <= 0

                if is_soft:
                    cqm.add_constraint(constraint_expr, label=label, weight=float(weight))
                else:
                    cqm.add_constraint(constraint_expr, label=label)
                count += 1

            except StructuredFallbackAllowed as e:
                if idx == 0:
                    logger.info(f"Constraint '{cid}': affine collector unsupported ({e}) → fallback")
                return 0  # 첫 실패 시 전체 fallback (일부만 affine이면 일관성 문제)
            except StructuredDataError as e:
                _err_key = f"{cid}:{e}"
                if _err_key not in self._affine_error_logged:
                    logger.warning(f"Constraint '{cid}' idx={idx}: data error in affine collector: {e}")
                    self._affine_error_logged.add(_err_key)
                continue
            except Exception as e:
                if idx == 0:
                    logger.debug(f"Constraint '{cid}': affine collector failed ({e}) → fallback")
                return 0

        return count

    def _try_fast_no_overlap(self, cqm, var_map, con_def, set_map, remaining: int) -> int:
        """
        x[i1,j] + x[i2,j] <= 1 패턴 고속 처리.

        감지 조건:
          - for_each에 'overlap_pairs' 포함
          - lhs: {type:sum, terms: [{type:variable,id:X,indices:[a,c]}, {type:variable,id:X,indices:[b,c]}]}
          - rhs: {type:constant, value:1}
          - operator: <=

        build_constraint를 우회하여:
          1. 변수맵을 string-key dict으로 전처리 (O(1) 조회)
          2. overlap_pairs × J 순회, budget 초과 시 즉시 중단
        """
        if "overlap_pairs" not in con_def.get("for_each", ""):
            return 0

        lhs_node = con_def.get("lhs", {})
        rhs_node = con_def.get("rhs", {})
        if con_def.get("operator", "") != "<=":
            return 0

        # ── RHS: 상수 1 감지 (두 형식 지원) ──
        rhs_val = None
        if isinstance(rhs_node, dict):
            if rhs_node.get("type") == "constant":
                rhs_val = rhs_node.get("value", 1)
            elif "value" in rhs_node and len(rhs_node) <= 2:
                rhs_val = rhs_node["value"]
        if rhs_val is None:
            return 0

        # ── LHS: x[i1,d] + x[i2,d] 감지 (두 형식 지원) ──
        # 형식1: {"type":"sum", "terms":[{"type":"variable","id":"x","indices":["i1","d"]}, ...]}
        # 형식2: {"add":[{"var":{"name":"x","index":"[i1,d]"}}, ...]}
        terms = []
        if lhs_node.get("type") == "sum":
            terms = lhs_node.get("terms", [])
        elif "add" in lhs_node and isinstance(lhs_node["add"], list):
            terms = lhs_node["add"]

        if len(terms) != 2:
            return 0

        def _extract_var_idx(term):
            """term에서 (var_name, [idx1, idx2]) 추출"""
            if term.get("type") == "variable":
                return term.get("id", ""), term.get("indices", [])
            if "var" in term and isinstance(term["var"], dict):
                vn = term["var"].get("name", term["var"].get("id", ""))
                idx_raw = term["var"].get("index", "")
                if isinstance(idx_raw, str):
                    idx_list = [s.strip() for s in idx_raw.strip("[]").split(",") if s.strip()]
                else:
                    idx_list = list(idx_raw) if idx_raw else []
                return vn, idx_list
            return None, []

        name0, idx0 = _extract_var_idx(terms[0])
        name1, idx1 = _extract_var_idx(terms[1])
        if not name0 or name0 != name1:
            return 0
        var_name = name0

        if len(idx0) != 2 or len(idx1) != 2 or idx0[1] != idx1[1]:
            return 0

        shared_idx = idx0[1]                        # "d"
        outer_set = set_map.get(shared_idx.upper(), [])  # J
        overlap_pairs = set_map.get("overlap_pairs", [])

        x_vars = var_map.get(var_name, {})
        if not isinstance(x_vars, dict) or not x_vars or not outer_set or not overlap_pairs:
            return 0

        # string-key 전처리: O(1) 조회를 위해 모든 키를 str tuple로 변환
        x_str_map = {}
        for key, var in x_vars.items():
            if isinstance(key, tuple):
                x_str_map[tuple(str(k) for k in key)] = var
            else:
                x_str_map[(str(key),)] = var

        cid = con_def.get("id") or con_def.get("name", "no_overlap")
        count = 0
        skipped = 0

        for pair in overlap_pairs:
            if count >= remaining:
                break
            i1_str = str(pair[0])
            i2_str = str(pair[1])
            for j_val in outer_set:
                if count >= remaining:
                    break
                j_str = str(j_val)
                x1 = x_str_map.get((i1_str, j_str))
                x2 = x_str_map.get((i2_str, j_str))
                if x1 is None or x2 is None:
                    skipped += 1
                    continue
                try:
                    cqm.add_constraint(x1 + x2 <= rhs_val, label=f"{cid}_{count}")
                    count += 1
                except Exception as e:
                    logger.debug(f"no_overlap {cid}_{count} failed: {e}")

        total_possible = len(overlap_pairs) * len(outer_set)
        logger.info(
            f"no_overlap fast-path: {count}/{total_possible} constraints "
            f"(pairs={len(overlap_pairs)}, J={len(outer_set)}, skipped={skipped})"
        )
        return count

    def _sort_overlap_pairs_by_duration(self, pairs, param_map, set_map) -> list:
        """
        overlap_pairs를 겹침 시간 기준 내림차순 정렬.
        정렬 후 예산 잘라낼 때 덜 중요한 쌍(짧게 겹치는)이 잘리도록 함.
        trip_dep_time / trip_arr_time 파라미터가 없으면 원본 순서 유지.
        """
        dep_times = param_map.get("trip_dep_time")
        arr_times = param_map.get("trip_arr_time")

        if not dep_times or not arr_times:
            return pairs

        def _get_time(times, trip_id):
            """dict 또는 list 형식 모두 지원하여 trip 시간 조회"""
            if isinstance(times, dict):
                v = times.get(trip_id)
                if v is None:
                    v = times.get(str(trip_id))
                if v is None and isinstance(trip_id, str) and trip_id.isdigit():
                    v = times.get(int(trip_id))
                return v
            # list 형식: trip_id를 인덱스로 사용 (0-based)
            try:
                return times[int(trip_id)]
            except (IndexError, ValueError, TypeError):
                return None

        def overlap_duration(pair):
            try:
                dep1 = _get_time(dep_times, pair[0])
                arr1 = _get_time(arr_times, pair[0])
                dep2 = _get_time(dep_times, pair[1])
                arr2 = _get_time(arr_times, pair[1])
                if any(t is None for t in (dep1, arr1, dep2, arr2)):
                    return 0
                return min(arr1, arr2) - max(dep1, dep2)
            except Exception:
                return 0

        sorted_pairs = sorted(pairs, key=overlap_duration, reverse=True)
        logger.info(
            f"overlap_pairs sorted by duration: {len(sorted_pairs)} pairs "
            f"(top overlap: {overlap_duration(sorted_pairs[0]) if sorted_pairs else 0:.0f}min)"
        )
        return sorted_pairs

    def _lookup_var(self, vmap: dict, key: tuple):
        """튜플 키로 변수 조회 (int/str 혼용 대응)"""
        if key in vmap:
            return vmap[key]
        str_key = tuple(str(k) for k in key)
        if str_key in vmap:
            return vmap[str_key]
        for vk in vmap:
            vk_t = vk if isinstance(vk, tuple) else (vk,)
            if len(vk_t) == len(key) and all(str(a) == str(b) for a, b in zip(vk_t, key)):
                return vmap[vk]
        return None

    # ── expression_parser 경유 CQM 제약 적용 ──

    def _parse_constraint_expr_cqm(self, cqm, var_map, con_def, ctx, max_count: int = 0) -> int:
        """
        expression 문자열을 expression_parser로 평가하여 CQM에 적용.
        OR-Tools의 parse_and_apply_expression과 동일한 로직이나,
        model.Add() 대신 cqm.add_constraint()를 사용.
        max_count > 0이면 해당 수만큼만 적용 (예산 관리).
        """
        from engine.compiler.expression_parser import _parse_for_each, _eval_expr

        expr_str = con_def.get("expression", "").strip()
        for_each_str = con_def.get("for_each", "")
        cid = con_def.get("id") or con_def.get("name", "unknown")
        category = con_def.get("category", "hard")
        weight = con_def.get("weight")

        if not expr_str:
            return 0

        op = None
        lhs_str = rhs_str = None
        for op_try in ['<=', '>=', '==']:
            if op_try in expr_str:
                parts = expr_str.split(op_try, 1)
                lhs_str = parts[0].strip()
                rhs_str = parts[1].strip()
                op = op_try
                break

        if not op:
            return 0

        bindings = _parse_for_each(for_each_str, ctx)
        total_bindings = len(bindings)
        if max_count > 0 and total_bindings > max_count:
            logger.warning(
                f"Constraint '{cid}': {total_bindings} bindings truncated to {max_count} (CQM budget)"
            )
            bindings = bindings[:max_count]

        count = 0
        for idx, binding in enumerate(bindings):
            try:
                lhs_val = _eval_expr(lhs_str, binding, ctx, var_map, None)
                rhs_val = _eval_expr(rhs_str, binding, ctx, var_map, None)
                label = f"{cid}_{idx}"
                self._add_cqm_constraint(cqm, lhs_val, op, rhs_val, label, category, weight)
                count += 1
            except Exception as e:
                logger.debug(f"CQM expression constraint {cid}[{idx}] failed: {e}")

        return count

    # ── 레거시 제약 파서 (폴백) ──

    def _parse_constraint_legacy(self, cqm, var_map, con_def, set_map, param_map) -> int:
        """레거시 expression 기반 파싱"""
        expr = con_def.get("expression", "").strip()
        cid = con_def.get("id") or con_def.get("name", "unknown")
        category = con_def.get("category", "hard")
        weight = con_def.get("weight")
        count = 0

        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for gk, gvars in groups.items():
                            label = f"{cid}_{gk}"
                            if category == "soft" and weight:
                                cqm.add_constraint(sum(gvars) == 1, label=label, weight=float(weight))
                            else:
                                cqm.add_constraint(sum(gvars) == 1, label=label)
                            count += 1
                        break

        elif "sum" in expr and "<=" in expr:
            nums = re.findall(r'<=\s*(\d+)', expr)
            ub = int(nums[0]) if nums else 10
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[-1], []).append(var)
                        for gk, gvars in groups.items():
                            label = f"{cid}_{gk}"
                            cqm.add_constraint(sum(gvars) <= ub, label=label)
                            count += 1
                        break

        return count
