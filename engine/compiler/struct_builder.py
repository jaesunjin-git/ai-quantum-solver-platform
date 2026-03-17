"""
Structured Constraint Builder
=============================
구조화된 제약 JSON (lhs/operator/rhs)을 솔버별 제약으로 변환.

3단계 Fallback:
  1단계: 구조화 필드 (이 모듈)
  2단계: expression -> AST 파서 (expr_evaluator.py)
  3단계: 정규식 패턴 매칭 (legacy)
"""
import math
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Scalar / Index Normalization Utilities ──

def coerce_scalar(value, *, name: str = "value"):
    """
    원소 값, 계수, 바운드를 Python scalar로 정규화.

    허용: int, float (finite)
    변환: numpy scalar → Python scalar, ndarray(size=1) → scalar
    거부: None, NaN, inf, ndarray(size>1), list(len>1)
    """
    try:
        import numpy as np
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, np.ndarray):
            if value.ndim == 0 or value.size == 1:
                value = value.reshape(-1)[0].item()
            else:
                from engine.compiler.errors import NonScalarBoundValueError
                raise NonScalarBoundValueError(f"{name}: ndarray(size={value.size})")
    except ImportError:
        pass

    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            value = value[0]
        else:
            from engine.compiler.errors import NonScalarBoundValueError
            raise NonScalarBoundValueError(f"{name}: {type(value).__name__}(len={len(value)})")

    if value is None:
        from engine.compiler.errors import NoneValueError
        raise NoneValueError(f"{name}: None")

    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        from engine.compiler.errors import NonFiniteValueError
        raise NonFiniteValueError(f"{name}: {value}")

    # 최종 numpy scalar 변환 (이중 안전)
    try:
        import numpy as np
        if isinstance(value, np.generic):
            value = value.item()
    except ImportError:
        pass

    return value


def normalize_index_atom(v):
    """numpy scalar → Python scalar 변환. 문자열↔숫자 변환 금지."""
    try:
        import numpy as np
        if isinstance(v, np.generic):
            return v.item()
    except ImportError:
        pass
    return v


def normalize_index_key(key):
    """인덱스 키 정규화. numpy scalar → Python scalar, list → tuple."""
    if isinstance(key, tuple):
        return tuple(normalize_index_atom(x) for x in key)
    if isinstance(key, list):
        return tuple(normalize_index_atom(x) for x in key)
    return normalize_index_atom(key)


class BuildContext:
    """변수맵, 파라미터맵, 세트맵을 관리하는 빌드 컨텍스트"""

    def __init__(self, var_map: Dict[str, Any], param_map: Dict[str, Any], set_map: Dict[str, List], model=None):
        self.var_map = var_map
        self.param_map = param_map
        self.set_map = set_map
        self.model = model  # optional: CP-SAT/LP model for auxiliary variable creation
        self.missing_params: set = set()  # 바인딩 실패한 파라미터 이름 추적

        # ── 역색인 캐시: set value → index (O(1) 조회용) ──
        # get_param_indexed에서 list params의 list.index() O(N) 반복을 제거
        self._set_index_cache: Dict[str, Dict] = {}
        for _sid, _svals in set_map.items():
            if isinstance(_svals, (list, tuple)):
                _cache: Dict = {}
                for _idx, _v in enumerate(_svals):
                    _cache[_v] = _idx
                    _cache[str(_v)] = _idx
                    try:
                        _cache[int(_v)] = _idx
                    except (ValueError, TypeError):
                        pass
                self._set_index_cache[_sid] = _cache

        # ── 변수 str-key 캐시: str tuple key → original key (O(1) 조회용) ──
        # get_var fallback 전체 순회를 제거
        self._var_str_cache: Dict[str, Dict] = {}
        for _vname, _vmap in var_map.items():
            if isinstance(_vmap, dict):
                _sc: Dict = {}
                for _k in _vmap:
                    _sk = tuple(str(x) for x in _k) if isinstance(_k, tuple) else str(_k)
                    _sc[_sk] = _k
                self._var_str_cache[_vname] = _sc

    def get_set(self, name: str) -> List:
        return self.set_map.get(name, [])

    def get_param_scalar(self, name: str) -> Any:
        val = self.param_map.get(name)
        if isinstance(val, dict):
            return None
        if val is None:
            return None
        # 리스트/배열이면 스칼라가 아님 → None 반환 (indexed로 처리해야 함)
        if isinstance(val, (list, tuple)):
            logger.warning(f"Parameter '{name}' is array (len={len(val)}), not scalar")
            return None
        try:
            import numpy as np
            if isinstance(val, np.ndarray):
                logger.warning(f"Parameter '{name}' is ndarray (shape={val.shape}), not scalar")
                return None
        except ImportError:
            pass
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                try:
                    return float(val)
                except ValueError:
                    return val
        try:
            import numpy as np
            if isinstance(val, (np.integer, np.floating)):
                val = int(val) if float(val) == int(float(val)) else float(val)
        except ImportError:
            pass
        if isinstance(val, float) and val == int(val):
            return int(val)
        return val
    def get_param_indexed(self, name: str, key) -> Any:
        key = normalize_index_key(key)
        val = self.param_map.get(name)
        if isinstance(val, dict):
            if key in val:
                result = val[key]
            else:
                str_key = str(key) if not isinstance(key, tuple) else tuple(str(k) for k in key)
                if str_key in val:
                    result = val[str_key]
                else:
                    return 0
            if isinstance(result, str):
                try:
                    return int(result)
                except ValueError:
                    try:
                        return float(result)
                    except ValueError:
                        return result
            return result
        if val is not None:
            # list/tuple인 경우: 역색인 캐시로 O(1) 조회
            if isinstance(val, (list, tuple)):
                # 캐시에서 key → index 탐색 (O(1))
                _str_key = str(key)
                for _sid, _idx_cache in self._set_index_cache.items():
                    _idx = _idx_cache.get(key)
                    if _idx is None:
                        _idx = _idx_cache.get(_str_key)
                    if _idx is not None and _idx < len(val):
                        _r = val[_idx]
                        return int(_r) if isinstance(_r, float) and _r == int(_r) else _r
                # fallback: 직접 정수 인덱스
                try:
                    int_key = int(key)
                    if 0 <= int_key < len(val):
                        result = val[int_key]
                        return int(result) if isinstance(result, float) and result == int(result) else result
                except (ValueError, TypeError):
                    pass
                logger.warning(
                    f"Parameter '{name}': array len={len(val)}, "
                    f"key={key} not mappable to index"
                )
                return 0
            if isinstance(val, str):
                try:
                    return int(val)
                except ValueError:
                    try:
                        return float(val)
                    except ValueError:
                        return val
            return val
        return 0

    def get_var(self, name: str, key) -> Any:
        vmap = self.var_map.get(name)
        if not isinstance(vmap, dict):
            return 0
        key = normalize_index_key(key)
        if key in vmap:
            return vmap[key]
        str_key = tuple(str(k) for k in key) if isinstance(key, tuple) else str(key)
        if str_key in vmap:
            return vmap[str_key]
        # str-key 캐시로 O(1) 조회 (기존 전체 순회 대체)
        _sc = self._var_str_cache.get(name)
        if _sc:
            orig = _sc.get(str_key)
            if orig is not None:
                return vmap[orig]
        return 0


def parse_for_each(for_each: str, ctx: BuildContext) -> List[Dict[str, Any]]:
    """for_each 문자열을 파싱하여 인덱스 바인딩 리스트 반환.
    단일 변수: 'i in I'
    튜플 변수: '(i1, i2) in overlap_pairs'  ← 괄호 안 쉼표를 구분자로 오인하지 않도록 처리.
    """
    if not for_each or not for_each.strip():
        return [{}]

    text = for_each.strip()
    text = re.sub(r'\bfor\b', '', text).strip()

    # 괄호 깊이를 추적하여 분리 (튜플 이터레이터의 내부 쉼표 보호)
    segments = []
    buf = ''
    paren_depth = 0
    for ch in text + ',':
        if ch == '(':
            paren_depth += 1
            buf += ch
        elif ch == ')':
            paren_depth -= 1
            buf += ch
        elif ch == ',' and paren_depth == 0:
            if buf.strip():
                segments.append(buf.strip())
            buf = ''
        else:
            buf += ch

    loop_specs = []
    for segment in segments:
        # 튜플 형태: (i1, i2) in set_name
        m = re.match(r'\(([^)]+)\)\s+in\s+(\w+)', segment)
        if m:
            var_names = [v.strip() for v in m.group(1).split(',')]
            set_name = m.group(2)
            values = ctx.get_set(set_name)
            if not values:
                logger.warning(f"Set '{set_name}' not found or empty")
            loop_specs.append(('tuple', var_names, values))
            continue
        # 단일 형태: i in I
        m = re.match(r'(\w+)\s+in\s+(\w+)', segment)
        if m:
            set_name = m.group(2)
            values = ctx.get_set(set_name)
            if not values:
                logger.warning(f"Set '{set_name}' not found or empty")
            loop_specs.append(('single', m.group(1), values))

    if not loop_specs:
        return [{}]

    result = [{}]
    for spec in loop_specs:
        new_result = []
        if spec[0] == 'tuple':
            _, var_names, values = spec
            for binding in result:
                for val in values:
                    nb = dict(binding)
                    if isinstance(val, (list, tuple)):
                        for vn, vv in zip(var_names, val):
                            nb[vn] = vv
                    else:
                        nb[var_names[0]] = val
                    new_result.append(nb)
        else:
            _, idx_name, values = spec
            for binding in result:
                for val in values:
                    new_result.append({**binding, idx_name: val})
        result = new_result
    return result


def parse_index_string(index_str: str) -> List[str]:
    """'[i,j]' -> ['i', 'j']"""
    if not index_str:
        return []
    cleaned = index_str.strip().strip('[]()').strip()
    if not cleaned:
        return []
    return [s.strip() for s in cleaned.split(',')]


def resolve_index(index_names: List[str], binding: Dict[str, Any]) -> tuple:
    """인덱스 이름을 현재 바인딩 값으로 치환"""
    resolved = []
    for name in index_names:
        if name in binding:
            resolved.append(binding[name])
        else:
            resolved.append(name)
    return tuple(resolved) if len(resolved) > 1 else (resolved[0],) if resolved else ()


def eval_node(node: Any, binding: Dict[str, Any], ctx: BuildContext) -> Any:
    """
    LHS/RHS 노드를 재귀적으로 평가.
    반환값: 숫자(int/float) 또는 솔버 변수 또는 솔버 표현식
    """
    if node is None:
        return 0

    if isinstance(node, (int, float)):
        return node

    if not isinstance(node, dict):
        return 0

    # value 노드
    if 'value' in node:
        return node['value']

    # var 노드
    if 'var' in node:
        var_info = node['var']
        if isinstance(var_info, dict):
            name = var_info.get('name', '')
            index_str = var_info.get('index', '')
        else:
            return 0
        index_names = parse_index_string(index_str)
        key = resolve_index(index_names, binding)
        return ctx.get_var(name, key[0] if len(key) == 1 else key)

    # param 노드
    if 'param' in node:
        param_info = node['param']
        if isinstance(param_info, dict):
            name = param_info.get('name', '')
            index_str = param_info.get('index', '')
        elif isinstance(param_info, str):
            name = param_info
            index_str = node.get('index', '')
        else:
            return 0

        def _to_int_if_whole(v):
            """CP-SAT 호환: float가 정수값이면 int로 변환"""
            if isinstance(v, float) and v == v:  # NaN 체크
                if v == int(v):
                    return int(v)
            return v

        if not index_str:
            val = ctx.get_param_scalar(name)
            if val is None:
                # scalar 실패 → binding에서 인덱스 추출하여 indexed 시도
                if binding:
                    for idx_key in binding.values():
                        indexed_val = ctx.get_param_indexed(name, idx_key)
                        if indexed_val != 0 or ctx.param_map.get(name) is not None:
                            return _to_int_if_whole(indexed_val)
                if name not in ctx.missing_params:
                    ctx.missing_params.add(name)
                    logger.warning(f"Parameter '{name}' not found in param_map, using 0")
                return 0
            return _to_int_if_whole(val)
        index_names = parse_index_string(index_str)
        key = resolve_index(index_names, binding)
        return _to_int_if_whole(ctx.get_param_indexed(name, key[0] if len(key) == 1 else key))

    # sum 노드
    if 'sum' in node:
        sum_val = node['sum']
        if isinstance(sum_val, str):
            # String expression sum (e.g., YAML: {sum: "y[j]*(1-is_night[j])", for_each: "j in J"})
            # Delegate to expression_parser which handles CP-SAT BoolVar multiplication
            from engine.compiler.expression_parser import _parse_for_each, _eval_expr
            for_each_str = node.get('for_each', '')
            bindings = _parse_for_each(for_each_str, ctx)
            result = None
            for inner_binding in bindings:
                merged = {**binding, **inner_binding}
                str_merged = {k: str(v) for k, v in merged.items()}
                term = _eval_expr(sum_val, str_merged, ctx, ctx.var_map, ctx.model)
                if result is None:
                    result = term
                else:
                    result = result + term
            return result if result is not None else 0
        return eval_sum_node(sum_val, binding, ctx)

    # multiply 노드
    if 'multiply' in node:
        operands = node['multiply']
        if isinstance(operands, list) and len(operands) >= 2:
            result = eval_node(operands[0], binding, ctx)
            for op in operands[1:]:
                val = eval_node(op, binding, ctx)
                result = result * val
            return result
        return 0

    # add 노드
    if 'add' in node:
        operands = node['add']
        if isinstance(operands, list) and len(operands) >= 2:
            result = eval_node(operands[0], binding, ctx)
            for op in operands[1:]:
                val = eval_node(op, binding, ctx)
                result = result + val
            return result
        return 0

    # subtract 노드
    if 'subtract' in node:
        operands = node['subtract']
        if isinstance(operands, list) and len(operands) >= 2:
            left = eval_node(operands[0], binding, ctx)
            right = eval_node(operands[1], binding, ctx)
            return left - right
        return 0

    # ── 'type:' 기반 형식 처리 (model.json이 사용하는 포맷) ──
    if 'type' in node:
        node_type = node['type']

        if node_type == 'constant':
            return node.get('value', 0)

        elif node_type in ('variable', 'var'):
            name = node.get('id', node.get('name', ''))
            indices = node.get('indices', [])
            if not indices:
                vmap = ctx.var_map.get(name)
                return vmap if (vmap is not None and not isinstance(vmap, dict)) else 0
            key = resolve_index(indices, binding)
            return ctx.get_var(name, key[0] if len(key) == 1 else key)

        elif node_type in ('parameter', 'param'):
            name = node.get('id', node.get('name', ''))
            indices = node.get('indices', [])
            if not indices:
                val = ctx.get_param_scalar(name)
                return val if val is not None else 0
            key = resolve_index(indices, binding)
            return ctx.get_param_indexed(name, key[0] if len(key) == 1 else key)

        elif node_type == 'sum':
            if 'terms' in node:
                # 명시적 terms 리스트 합산
                result = None
                for term in node['terms']:
                    tv = eval_node(term, binding, ctx)
                    result = tv if result is None else result + tv
                return result if result is not None else 0
            elif 'variable' in node:
                # {"type":"sum","variable":"x","sum_over":"j","indices":["i","j"],"coefficient":"trip_duration[i]"}
                var_name = node['variable']
                indices = node.get('indices', [])
                sum_over_var = node.get('sum_over', '')
                coeff_str = node.get('coefficient', '')
                # sum_over_var (예: "j") → 대문자 set 이름 "J"
                set_name = sum_over_var.upper() if sum_over_var else ''
                set_vals = ctx.get_set(set_name)
                if not set_vals:
                    return 0
                result = None
                for sv in set_vals:
                    local_binding = {**binding, sum_over_var: sv}
                    key = resolve_index(indices, local_binding)
                    vv = ctx.get_var(var_name, key[0] if len(key) == 1 else key)
                    if coeff_str:
                        from engine.compiler.expression_parser import _eval_expr as _ep_eval
                        coeff_val = _ep_eval(coeff_str, local_binding, ctx, ctx.var_map, None)
                        term = coeff_val * vv
                    else:
                        term = vv
                    result = term if result is None else result + term
                return result if result is not None else 0

        elif node_type == 'product':
            terms = node.get('terms', [])
            if not terms:
                return 0
            result = eval_node(terms[0], binding, ctx)
            for term in terms[1:]:
                result = result * eval_node(term, binding, ctx)
            return result

        elif node_type == 'subtract':
            terms = node.get('terms', [])
            if len(terms) < 2:
                return 0
            result = eval_node(terms[0], binding, ctx)
            for term in terms[1:]:
                result = result - eval_node(term, binding, ctx)
            return result

        elif node_type == 'expression':
            expr_str = node.get('expr', node.get('expression', ''))
            if expr_str:
                from engine.compiler.expression_parser import _eval_expr as _ep_eval
                return _ep_eval(expr_str, binding, ctx, ctx.var_map, None)
            return 0

    logger.warning(f"Unknown node type: {list(node.keys())}")
    return 0


def eval_sum_node(sum_info: Dict, binding: Dict[str, Any], ctx: BuildContext) -> Any:
    """sum 노드 평가: var, index, over, coeff"""
    var_name = sum_info.get('var', '')
    index_str = sum_info.get('index', '')
    over_str = sum_info.get('over', '')
    coeff_node = sum_info.get('coeff')

    index_names = parse_index_string(index_str)

    # over 파싱: "j in J" 또는 "j in J, k in K"
    over_parts = re.split(r'\s*,\s*', over_str) if over_str else []
    over_specs = []
    for part in over_parts:
        part = part.strip()
        m = re.match(r'(\w+)\s+in\s+(\w+)', part)
        if m:
            over_specs.append((m.group(1), ctx.get_set(m.group(2))))

    if not over_specs:
        logger.warning(f"sum node has no valid 'over': {over_str}")
        return 0

    # over 인덱스의 모든 조합 생성
    over_bindings = [{}]
    for idx_name, values in over_specs:
        new_bindings = []
        for ob in over_bindings:
            for val in values:
                new_bindings.append({**ob, idx_name: val})
        over_bindings = new_bindings

    # 합산
    terms = []
    for ob in over_bindings:
        local_binding = {**binding, **ob}
        key = resolve_index(index_names, local_binding)
        var_val = ctx.get_var(var_name, key[0] if len(key) == 1 else key)

        if coeff_node:
            coeff_val = eval_node(coeff_node, local_binding, ctx)
            term = coeff_val * var_val
        else:
            term = var_val

        terms.append(term)

    if not terms:
        return 0

    result = terms[0]
    for t in terms[1:]:
        result = result + t
    return result


def build_constraint(
    con_def: Dict,
    ctx: BuildContext,
    max_instances: int = 0,
) -> List[Tuple[Any, str, Any]]:
    """
    구조화된 제약 JSON -> (lhs_expr, operator, rhs_expr) 리스트.
    for_each가 있으면 바인딩별로 여러 제약 생성.

    max_instances > 0이면 바인딩 생성 후 해당 수만큼만 평가 (예산 초과 방지).

    반환: [(lhs, op, rhs), ...] 또는 빈 리스트 (파싱 실패 시)
    """
    lhs_node = con_def.get('lhs')
    op = con_def.get('operator', '==')
    rhs_node = con_def.get('rhs')
    for_each = con_def.get('for_each', '')

    if lhs_node is None or rhs_node is None:
        return []

    # ── overlap_pairs 사전 필터링 지원 ──
    overlap_pairs = con_def.get("_overlap_pairs")
    if overlap_pairs:
        import re as _re
        # for_each에서 i, j 루프를 제거하고 나머지(d in D 등)만 남김
        remaining_fe = for_each
        for var in ("i", "j"):
            remaining_fe = _re.sub(
                rf"\b{var}\s+in\s+\w+\s*,?\s*", "", remaining_fe
            ).strip().strip(",").strip()
        extra_bindings = parse_for_each(remaining_fe, ctx) if remaining_fe else [{}]

        constraints = []
        _limit = max_instances if max_instances > 0 else float('inf')
        for pair in overlap_pairs:
            if len(constraints) >= _limit:
                break
            pair_bind = {"i": pair[0], "j": pair[1]}
            for eb in extra_bindings:
                if len(constraints) >= _limit:
                    break
                binding = {**pair_bind, **eb}
                try:
                    lhs_val = eval_node(lhs_node, binding, ctx)
                    rhs_val = eval_node(rhs_node, binding, ctx)
                    constraints.append((lhs_val, op, rhs_val))
                except Exception as e:
                    logger.warning(
                        f"Constraint eval failed for overlap binding {binding}: {e}"
                    )
        _total_possible = len(overlap_pairs) * len(extra_bindings)
        logger.info(
            f"Overlap filter: {len(constraints)}/{_total_possible} constraints"
            f"{f' (truncated to {max_instances})' if len(constraints) < _total_possible else ''}"
        )
        return constraints

    # ── 기본 경로: 기존 for_each 파싱 ──
    bindings = parse_for_each(for_each, ctx)
    # max_instances 제한: 바인딩 생성 후 즉시 잘라냄 (eval_node 비용 절감)
    if max_instances > 0 and len(bindings) > max_instances:
        logger.warning(
            f"build_constraint: {len(bindings)} bindings truncated to {max_instances} (max_instances)"
        )
        bindings = bindings[:max_instances]
    constraints = []

    for binding in bindings:
        try:
            lhs_val = eval_node(lhs_node, binding, ctx)
            rhs_val = eval_node(rhs_node, binding, ctx)
            constraints.append((lhs_val, op, rhs_val))
        except Exception as e:
            logger.warning(f"Constraint eval failed for binding {binding}: {e}")

    return constraints


def build_objective(
    obj_def: Dict,
    ctx: BuildContext,
) -> Tuple[Optional[str], Any]:
    """
    목적함수 평가.
    반환: (type, expression) 여기서 type은 'minimize' 또는 'maximize'
    """
    obj_type = obj_def.get('type', 'minimize')
    expr = obj_def.get('expression', '')

    # 구조화된 lhs가 있으면 사용
    lhs_node = obj_def.get('lhs')
    if lhs_node:
        val = eval_node(lhs_node, {}, ctx)
        return (obj_type, val)

    # lhs가 없으면 expression 문자열에서 파싱 시도
    # 패턴: sum(var[idx] for idx in SET)
    if expr:
        import re
        # sum(u[d] for d in D) 패턴
        m = re.match(r'sum\(\s*(\w+)\[(\w+)\]\s+for\s+(\w+)\s+in\s+(\w+)\s*\)', expr)
        if m:
            var_name, idx_name, loop_var, set_name = m.groups()
            set_vals = ctx.get_set(set_name)
            if set_vals is not None:
                total = None
                for sv in set_vals:
                    v = ctx.get_var(var_name, sv)
                    if v is not None:
                        total = v if total is None else total + v
                if total is not None:
                    logger.info(f"Objective parsed from expression: {expr}")
                    return (obj_type, total)

        # sum(coeff[i]*var[i,d] for i in I for d in D) 패턴
        m2 = re.match(
            r'sum\(\s*(\w+)\[(\w+)\]\s*\*\s*(\w+)\[(\w+(?:,\w+)*)\]'
            r'\s+for\s+(\w+)\s+in\s+(\w+)\s+for\s+(\w+)\s+in\s+(\w+)\s*\)',
            expr
        )
        if m2:
            coeff_name, c_idx, var_name, v_indices, l1, s1, l2, s2 = m2.groups()
            set1 = ctx.get_set(s1)
            set2 = ctx.get_set(s2)
            if set1 is not None and set2 is not None:
                total = None
                for sv1 in set1:
                    coeff = ctx.get_param_indexed(coeff_name, sv1)
                    if isinstance(coeff, float) and coeff == int(coeff):
                        coeff = int(coeff)
                    for sv2 in set2:
                        v = ctx.get_var(var_name, (sv1, sv2))
                        term = coeff * v if coeff != 0 else 0
                        if term != 0:
                            total = term if total is None else total + term
                if total is not None:
                    logger.info(f"Objective parsed from expression (2-loop): {expr}")
                    return (obj_type, total)

    # 파싱 실패
    return (obj_type, None)


def apply_constraint_cpsat(model, lhs, op: str, rhs) -> bool:
    """CP-SAT 모델에 제약 추가"""
    try:
        # CP-SAT은 정수만 허용 - float를 int로 변환
        if isinstance(rhs, float):
            rhs = int(rhs)
        if isinstance(lhs, float):
            lhs = int(lhs)
        if op == '==':
            model.Add(lhs == rhs)
        elif op == '<=':
            model.Add(lhs <= rhs)
        elif op == '>=':
            model.Add(lhs >= rhs)
        elif op == '<':
            model.Add(lhs < rhs)
        elif op == '>':
            model.Add(lhs > rhs)
        elif op == '!=':
            model.Add(lhs != rhs)
        else:
            logger.warning(f"Unknown operator: {op}")
            return False
        return True
    except Exception as e:
        logger.warning(f"CP-SAT constraint add failed: {e}")
        return False


def apply_constraint_lp(solver, lhs, op: str, rhs, name: str = "") -> bool:
    """LP/MIP 솔버에 제약 추가 - pywraplp 연산자 오버로딩 활용"""
    try:
        # pywraplp의 Variable과 LinearExpr은 <=, >=, == 연산자를 지원
        # solver.Add()로 직접 추가 가능
        if op == '==':
            solver.Add(lhs == rhs, name)
        elif op == '<=':
            solver.Add(lhs <= rhs, name)
        elif op == '>=':
            solver.Add(lhs >= rhs, name)
        elif op == '<':
            solver.Add(lhs <= rhs - 1, name)
        elif op == '>':
            solver.Add(lhs >= rhs + 1, name)
        elif op == '!=':
            logger.warning(f"LP does not support != operator natively, skipping {name}")
            return False
        else:
            logger.warning(f"Unknown operator: {op}")
            return False
        return True
    except Exception as e:
        logger.warning(f"LP constraint add failed: {e}")
        return False


def build_constraints_batch(
    constraints_def: List[Dict],
    ctx: BuildContext,
) -> Dict[str, Any]:
    """
    제약 리스트 전체를 처리하여 결과 요약 반환.
    
    반환: {
        'constraints': [(lhs, op, rhs), ...],
        'applied': int,
        'failed': int,
        'failed_names': [str],
        'warnings': [str],
    }
    """
    all_constraints = []
    applied = 0
    failed = 0
    failed_names = []
    warnings = []

    for con_def in constraints_def:
        name = con_def.get('name', 'unknown')
        has_struct = con_def.get('lhs') is not None and con_def.get('rhs') is not None

        if has_struct:
            try:
                results = build_constraint(con_def, ctx)
                if results:
                    all_constraints.extend(results)
                    applied += 1
                    logger.info(f"Constraint '{name}': {len(results)} instances generated (structured)")
                else:
                    failed += 1
                    failed_names.append(name)
                    warnings.append(f"Constraint '{name}': structured build returned 0 instances")
            except Exception as e:
                failed += 1
                failed_names.append(name)
                warnings.append(f"Constraint '{name}': structured build error - {e}")
        else:
            failed += 1
            failed_names.append(name)
            warnings.append(f"Constraint '{name}': no structured fields (lhs/rhs missing)")

    return {
        'constraints': all_constraints,
        'applied': applied,
        'failed': failed,
        'failed_names': failed_names,
        'warnings': warnings,
    }
