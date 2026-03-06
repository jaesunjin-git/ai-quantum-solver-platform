# engine/compiler/expression_parser.py
# 범용 수식 문자열 파서: constraints.yaml의 expression을 OR-Tools LP 제약으로 변환

PARSER_CODE = '''
import re
import logging

logger = logging.getLogger(__name__)


def parse_and_apply_expression(solver, expr_str, for_each_str, ctx, var_map):
    """
    expression 문자열 + for_each를 파싱하여 LP solver에 제약 추가
    
    Returns: 추가된 제약 수 (int)
    """
    # 1) 비교 연산자로 lhs/rhs 분리
    for op in ["<=", ">=", "=="]:
        if op in expr_str:
            parts = expr_str.split(op, 1)
            lhs_str = parts[0].strip()
            rhs_str = parts[1].strip()
            break
    else:
        logger.warning(f"No comparison operator in: {expr_str}")
        return 0

    # 2) for_each 바인딩 생성
    bindings = _parse_for_each(for_each_str, ctx)
    
    count = 0
    for binding in bindings:
        try:
            lhs_val = _eval_expr(lhs_str, binding, ctx, var_map, solver)
            rhs_val = _eval_expr(rhs_str, binding, ctx, var_map, solver)
            
            if op == "<=":
                solver.Add(lhs_val <= rhs_val)
            elif op == ">=":
                solver.Add(lhs_val >= rhs_val)
            elif op == "==":
                solver.Add(lhs_val == rhs_val)
            count += 1
        except Exception as e:
            logger.debug(f"Expression apply failed for binding {binding}: {e}")
    
    return count


def _parse_for_each(for_each_str, ctx):
    """for_each 문자열을 바인딩 리스트로 변환"""
    if not for_each_str or not for_each_str.strip():
        return [{}]
    
    text = for_each_str.strip()
    # "(i1,i2) in overlap_pairs, j in J" 같은 패턴 처리
    segments = []
    buf = ""
    paren_depth = 0
    for ch in text + ",":
        if ch == "(":
            paren_depth += 1
            buf += ch
        elif ch == ")":
            paren_depth -= 1
            buf += ch
        elif ch == "," and paren_depth == 0:
            if buf.strip():
                segments.append(buf.strip())
            buf = ""
        else:
            buf += ch
    
    # 각 segment 파싱
    all_specs = []
    for seg in segments:
        # 패턴1: (i1,i2) in set_name
        m = re.match(r"\\(([^)]+)\\)\\s+in\\s+(\\w+)", seg)
        if m:
            var_names = [v.strip() for v in m.group(1).split(",")]
            set_name = m.group(2)
            set_vals = ctx.get_set(set_name)
            all_specs.append(("tuple", var_names, set_vals))
            continue
        # 패턴2: var in set_name
        m = re.match(r"(\\w+)\\s+in\\s+(\\w+)", seg)
        if m:
            var_name = m.group(1)
            set_name = m.group(2)
            set_vals = ctx.get_set(set_name)
            all_specs.append(("single", var_name, set_vals))
            continue
        logger.warning(f"Cannot parse for_each segment: {seg}")
    
    # cross product
    bindings = [{}]
    for spec in all_specs:
        new_bindings = []
        if spec[0] == "tuple":
            var_names, set_vals = spec[1], spec[2]
            for b in bindings:
                for val in set_vals:
                    nb = dict(b)
                    if isinstance(val, (list, tuple)):
                        for vn, vv in zip(var_names, val):
                            nb[vn] = str(vv)
                    else:
                        nb[var_names[0]] = str(val)
                    new_bindings.append(nb)
        else:  # single
            var_name, set_vals = spec[1], spec[2]
            for b in bindings:
                for val in set_vals:
                    nb = dict(b)
                    nb[var_name] = str(val)
                    new_bindings.append(nb)
        bindings = new_bindings
    
    return bindings


def _eval_expr(expr_str, binding, ctx, var_map, solver):
    """수식 문자열을 재귀적으로 평가하여 OR-Tools 표현식 반환"""
    expr_str = expr_str.strip()
    
    # 괄호 제거 (최외곽)
    while expr_str.startswith("(") and _matching_paren(expr_str) == len(expr_str) - 1:
        expr_str = expr_str[1:-1].strip()
    
    # sum(...) 처리
    if expr_str.startswith("sum("):
        return _eval_sum(expr_str, binding, ctx, var_map, solver)
    
    # +/- 연산 (괄호 밖에서 분리)
    split_pos = _find_top_level_addop(expr_str)
    if split_pos is not None:
        pos, op_char = split_pos
        left = _eval_expr(expr_str[:pos], binding, ctx, var_map, solver)
        right = _eval_expr(expr_str[pos+1:], binding, ctx, var_map, solver)
        if op_char == "+":
            return left + right
        else:
            return left - right
    
    # * 연산 (괄호 밖에서 분리)
    mul_pos = _find_top_level_mul(expr_str)
    if mul_pos is not None:
        left = _eval_expr(expr_str[:mul_pos], binding, ctx, var_map, solver)
        right = _eval_expr(expr_str[mul_pos+1:], binding, ctx, var_map, solver)
        return left * right
    
    # 숫자 리터럴
    try:
        val = float(expr_str)
        return int(val) if val == int(val) else val
    except ValueError:
        pass
    
    # 변수/파라미터 참조: name[index]
    m = re.match(r"(\\w+)\\[([^\\]]+)\\]", expr_str)
    if m:
        name = m.group(1)
        idx_str = m.group(2)
        idx_parts = [_resolve_binding(p.strip(), binding) for p in idx_str.split(",")]
        
        # 변수인지 확인
        if name in var_map and isinstance(var_map[name], dict):
            key = tuple(idx_parts) if len(idx_parts) > 1 else idx_parts[0]
            var = _get_var(var_map[name], key)
            if var is not None:
                return var
        
        # 파라미터 (indexed)
        param_val = ctx.get_param_indexed(name, idx_parts[0] if len(idx_parts) == 1 else tuple(idx_parts))
        if param_val != 0 or name in ctx.param_map:
            return _to_num(param_val)
        
        return 0
    
    # 단순 이름 (파라미터 scalar 또는 바인딩 변수)
    resolved = _resolve_binding(expr_str, binding)
    
    # 파라미터 scalar
    scalar = ctx.get_param_scalar(resolved) if hasattr(ctx, "get_param_scalar") else None
    if scalar is None:
        scalar = ctx.get_param_scalar(expr_str)
    if scalar is not None:
        return _to_num(scalar)
    
    # 숫자로 변환 시도 (바인딩 결과)
    try:
        val = float(resolved)
        return int(val) if val == int(val) else val
    except ValueError:
        pass
    
    logger.debug(f"Cannot resolve: {expr_str} (binding={binding})")
    return 0


def _eval_sum(expr_str, binding, ctx, var_map, solver):
    """sum(... for var in Set) 평가"""
    # sum( body for var in Set ) 파싱
    inner = expr_str[4:]  # remove "sum("
    if inner.endswith(")"):
        inner = inner[:-1]
    
    # " for " 로 분리
    for_idx = inner.rfind(" for ")
    if for_idx < 0:
        return 0
    
    body_str = inner[:for_idx].strip()
    iter_str = inner[for_idx+5:].strip()
    
    # iter 파싱: "i in I"
    m = re.match(r"(\\w+)\\s+in\\s+(\\w+)", iter_str)
    if not m:
        return 0
    
    iter_var = m.group(1)
    iter_set = m.group(2)
    set_vals = ctx.get_set(iter_set)
    
    if not set_vals:
        return 0
    
    result = None
    for val in set_vals:
        local_binding = dict(binding)
        local_binding[iter_var] = str(val)
        term = _eval_expr(body_str, local_binding, ctx, var_map, solver)
        if result is None:
            result = term
        else:
            result = result + term
    
    return result if result is not None else 0


def _find_top_level_addop(expr):
    """괄호/sum() 밖의 마지막 +/- 위치 찾기 (우→좌)"""
    paren = 0
    last_pos = None
    last_op = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif paren == 0 and ch in "+-" and i > 0:
            # 앞 문자가 연산자가 아닌지 확인 (* 뒤의 -는 단항)
            prev = expr[i-1].strip() if i > 0 else ""
            if prev and prev not in "*+-/(":
                last_pos = i
                last_op = ch
        i += 1
    return (last_pos, last_op) if last_pos is not None else None


def _find_top_level_mul(expr):
    """괄호 밖의 마지막 * 위치"""
    paren = 0
    last_pos = None
    for i, ch in enumerate(expr):
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif paren == 0 and ch == "*":
            last_pos = i
    return last_pos


def _matching_paren(s):
    """첫 ( 에 매칭되는 ) 위치"""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _resolve_binding(name, binding):
    """바인딩에서 변수명 치환"""
    return binding.get(name, name)


def _get_var(vmap, key):
    """var_map에서 변수 찾기 (문자열 키 호환)"""
    if key in vmap:
        return vmap[key]
    str_key = str(key) if not isinstance(key, tuple) else tuple(str(k) for k in key)
    if str_key in vmap:
        return vmap[str_key]
    for vk in vmap:
        vk_t = vk if isinstance(vk, tuple) else (vk,)
        key_t = key if isinstance(key, tuple) else (key,)
        if len(vk_t) == len(key_t) and all(str(a) == str(b) for a, b in zip(vk_t, key_t)):
            return vmap[vk]
    return None


def _to_num(val):
    """숫자 변환"""
    if val is None:
        return 0
    try:
        f = float(val)
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return 0
'''

# 파일 저장
with open('engine/compiler/expression_parser.py', 'w', encoding='utf-8') as f:
    f.write(PARSER_CODE)

import py_compile
py_compile.compile('engine/compiler/expression_parser.py', doraise=True)
print('[OK] engine/compiler/expression_parser.py created')
print('syntax: OK')

# 줄 수 확인
lines = PARSER_CODE.strip().split('\\n')
print(f'Lines: {len(lines)}')
