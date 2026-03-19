import re
import logging

logger = logging.getLogger(__name__)

# ── CP-SAT BoolVar multiplication support ──
_mul_aux_counter = [0]


def _safe_multiply(left, right, model_or_solver):
    """
    Safely multiply two values.
    When both are CP-SAT solver variables/expressions (e.g., BoolVar * (1 - BoolVar)),
    creates auxiliary IntVar + add_multiplication_equality instead of using Python '*'.
    """
    # Both plain numbers → direct multiplication
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left * right

    # One side is a plain number → scalar * var (CP-SAT handles natively)
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        if isinstance(left, float) and left == int(left):
            left = int(left)
        if isinstance(right, float) and right == int(right):
            right = int(right)
        return left * right

    # Both solver objects → need auxiliary variable for CP-SAT
    if model_or_solver is None:
        logger.warning("Cannot multiply solver variables without model reference")
        return 0

    try:
        from ortools.sat.python import cp_model
        if not isinstance(model_or_solver, cp_model.CpModel):
            # LP solver — direct multiplication supported
            return left * right
    except ImportError:
        return left * right

    _mul_aux_counter[0] += 1
    idx = _mul_aux_counter[0]

    def _to_int_var(val, tag):
        """Convert LinearExpr to IntVar if needed (for add_multiplication_equality)."""
        if isinstance(val, cp_model.IntVar):
            return val
        # LinearExpr (e.g., 1 - BoolVar) → auxiliary IntVar with [0,1] bounds
        aux = model_or_solver.new_int_var(0, 1, f"_aux{idx}_{tag}")
        model_or_solver.add(aux == val)
        return aux

    try:
        lv = _to_int_var(left, "L")
        rv = _to_int_var(right, "R")
        pv = model_or_solver.new_int_var(0, 1, f"_prod{idx}")
        model_or_solver.add_multiplication_equality(pv, [lv, rv])
        return pv
    except Exception as e:
        logger.warning(f"CP-SAT var*var auxiliary failed: {e}")
        return 0


def parse_and_apply_expression(model_or_solver, expr_str, for_each_str, ctx, var_map):
    for op in ['<=', '>=', '==']:
        if op in expr_str:
            parts = expr_str.split(op, 1)
            lhs_str = parts[0].strip()
            rhs_str = parts[1].strip()
            break
    else:
        logger.warning(f'No comparison operator in: {expr_str}')
        return 0

    # ── big-M → OnlyEnforceIf 변환 감지 ──
    # 패턴: A <= B + big_m * (1 - x[i,j])  → Add(A <= B).OnlyEnforceIf(x[i,j])
    # 패턴: A >= B - big_m * (1 - x[i,j])  → Add(A >= B).OnlyEnforceIf(x[i,j])
    bigm_indicator = _detect_bigm_pattern(lhs_str, rhs_str, op)
    if bigm_indicator:
        logger.debug(f"big-M pattern detected: indicator={bigm_indicator['indicator_var_str']}")

    bindings = _parse_for_each(for_each_str, ctx)
    count = 0
    for binding in bindings:
        try:
            if bigm_indicator:
                # big-M 패턴 → OnlyEnforceIf 방식으로 적용
                _applied = _apply_bigm_as_indicator(
                    model_or_solver, bigm_indicator, op, binding, ctx, var_map
                )
                if _applied:
                    count += 1
                    continue

            lhs_val = _eval_expr(lhs_str, binding, ctx, var_map, model_or_solver)
            rhs_val = _eval_expr(rhs_str, binding, ctx, var_map, model_or_solver)
            if op == '<=':
                model_or_solver.Add(lhs_val <= rhs_val)
            elif op == '>=':
                model_or_solver.Add(lhs_val >= rhs_val)
            elif op == '==':
                model_or_solver.Add(lhs_val == rhs_val)
            count += 1
        except Exception as e:
            logger.debug(f'Expression apply failed for binding {binding}: {e}')
    return count


# ── big-M 패턴 감지 ────────────────────────────────────────

# big_m * (1 - x[...]) 또는 big_m * (1 - x_var) 패턴
_BIGM_PATTERN_PLUS = re.compile(
    r'(.+?)\s*\+\s*big_m\s*\*\s*\(1\s*-\s*(\w+\[[^\]]+\])\)\s*$'
)
_BIGM_PATTERN_MINUS = re.compile(
    r'(.+?)\s*-\s*big_m\s*\*\s*\(1\s*-\s*(\w+\[[^\]]+\])\)\s*$'
)


def _detect_bigm_pattern(lhs_str, rhs_str, op):
    """
    big-M 패턴을 감지하여 indicator 정보를 반환.

    지원 패턴:
      A <= B + big_m * (1 - x[i,j])  → indicator = x[i,j], core: A <= B
      A >= B - big_m * (1 - x[i,j])  → indicator = x[i,j], core: A >= B

    Returns:
      dict with {lhs_core, rhs_core, indicator_var_str, indicator_positive}
      or None if not a big-M pattern.
    """
    if op == '<=':
        m = _BIGM_PATTERN_PLUS.match(rhs_str)
        if m:
            return {
                'lhs_core': lhs_str,
                'rhs_core': m.group(1).strip(),
                'indicator_var_str': m.group(2).strip(),
                'indicator_positive': True,
            }
    elif op == '>=':
        m = _BIGM_PATTERN_MINUS.match(rhs_str)
        if m:
            return {
                'lhs_core': lhs_str,
                'rhs_core': m.group(1).strip(),
                'indicator_var_str': m.group(2).strip(),
                'indicator_positive': True,
            }
    return None


def _apply_bigm_as_indicator(model, bigm_info, op, binding, ctx, var_map):
    """
    big-M 패턴을 OnlyEnforceIf로 변환하여 적용.

    CP-SAT에서 big-M 대신 indicator constraint를 사용하면:
      - presolve가 global bound를 생성하지 않음
      - 수치 안정성 향상
      - propagation 강화
    """
    try:
        lhs_val = _eval_expr(bigm_info['lhs_core'], binding, ctx, var_map, model)
        rhs_val = _eval_expr(bigm_info['rhs_core'], binding, ctx, var_map, model)

        # indicator 변수 resolve
        indicator_str = bigm_info['indicator_var_str']
        indicator_var = _eval_expr(indicator_str, binding, ctx, var_map, model)

        # CP-SAT OnlyEnforceIf 적용
        if op == '<=':
            model.Add(lhs_val <= rhs_val).OnlyEnforceIf(indicator_var)
        elif op == '>=':
            model.Add(lhs_val >= rhs_val).OnlyEnforceIf(indicator_var)

        return True

    except Exception as e:
        logger.warning(f'big-M indicator apply FAILED (fallback to big-M): {e}')
        return False


def _parse_for_each(for_each_str, ctx):
    if not for_each_str or not for_each_str.strip():
        return [{}]
    text = for_each_str.strip()
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

    all_specs = []
    for seg in segments:
        m = re.match(r'\(([^)]+)\)\s+in\s+(\w+)', seg)
        if m:
            var_names = [v.strip() for v in m.group(1).split(',')]
            set_vals = ctx.get_set(m.group(2))
            all_specs.append(('tuple', var_names, set_vals))
            continue
        m = re.match(r'(\w+)\s+in\s+(\w+)', seg)
        if m:
            set_vals = ctx.get_set(m.group(2))
            all_specs.append(('single', m.group(1), set_vals))
            continue

    bindings = [{}]
    for spec in all_specs:
        new_bindings = []
        if spec[0] == 'tuple':
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
        else:
            var_name, set_vals = spec[1], spec[2]
            for b in bindings:
                for val in set_vals:
                    nb = dict(b)
                    nb[var_name] = str(val)
                    new_bindings.append(nb)
        bindings = new_bindings
    return bindings


def _eval_expr(expr_str, binding, ctx, var_map, model_or_solver):
    expr_str = expr_str.strip()
    while expr_str.startswith('(') and _matching_paren(expr_str) == len(expr_str) - 1:
        expr_str = expr_str[1:-1].strip()

    if expr_str.startswith('sum('):
        return _eval_sum(expr_str, binding, ctx, var_map, model_or_solver)

    split_pos = _find_top_level_addop(expr_str)
    if split_pos is not None:
        pos, op_char = split_pos
        left = _eval_expr(expr_str[:pos], binding, ctx, var_map, model_or_solver)
        right = _eval_expr(expr_str[pos+1:], binding, ctx, var_map, model_or_solver)
        return left + right if op_char == '+' else left - right

    mul_pos = _find_top_level_mul(expr_str)
    if mul_pos is not None:
        left = _eval_expr(expr_str[:mul_pos], binding, ctx, var_map, model_or_solver)
        right = _eval_expr(expr_str[mul_pos+1:], binding, ctx, var_map, model_or_solver)
        return _safe_multiply(left, right, model_or_solver)

    try:
        val = float(expr_str)
        return int(val) if val == int(val) else val
    except ValueError:
        pass

    m = re.match(r'(\w+)\[([^\]]+)\]', expr_str)
    if m:
        name = m.group(1)
        idx_parts = [_resolve_binding(p.strip(), binding) for p in m.group(2).split(',')]
        if name in var_map and isinstance(var_map[name], dict):
            key = tuple(idx_parts) if len(idx_parts) > 1 else idx_parts[0]
            var = _get_var(var_map[name], key)
            if var is not None:
                return var
        param_val = ctx.get_param_indexed(name, idx_parts[0] if len(idx_parts) == 1 else tuple(idx_parts))
        if param_val != 0 or name in ctx.param_map:
            return _to_num(param_val)
        return 0

    resolved = _resolve_binding(expr_str, binding)
    scalar = ctx.get_param_scalar(resolved)
    if scalar is None:
        scalar = ctx.get_param_scalar(expr_str)
    if scalar is not None:
        return _to_num(scalar)

    try:
        val = float(resolved)
        return int(val) if val == int(val) else val
    except ValueError:
        pass
    return 0


def _eval_sum(expr_str, binding, ctx, var_map, model_or_solver):
    inner = expr_str[4:]
    if inner.endswith(')'):
        inner = inner[:-1]
    for_idx = inner.rfind(' for ')
    if for_idx < 0:
        return 0
    body_str = inner[:for_idx].strip()
    iter_str = inner[for_idx+5:].strip()
    m = re.match(r'(\w+)\s+in\s+(\w+)', iter_str)
    if not m:
        return 0
    iter_var = m.group(1)
    set_vals = ctx.get_set(m.group(2))
    if not set_vals:
        return 0
    result = None
    for val in set_vals:
        local_binding = dict(binding)
        local_binding[iter_var] = str(val)
        term = _eval_expr(body_str, local_binding, ctx, var_map, model_or_solver)
        if result is None:
            result = term
        else:
            result = result + term
    return result if result is not None else 0


def _find_top_level_addop(expr):
    paren = 0
    last_pos = None
    last_op = None
    for i, ch in enumerate(expr):
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        elif paren == 0 and ch in '+-' and i > 0:
            prev = expr[i-1]
            if prev not in '*+-/(':
                last_pos = i
                last_op = ch
    return (last_pos, last_op) if last_pos is not None else None


def _find_top_level_mul(expr):
    paren = 0
    last_pos = None
    for i, ch in enumerate(expr):
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        elif paren == 0 and ch == '*':
            last_pos = i
    return last_pos


def _matching_paren(s):
    depth = 0
    for i, ch in enumerate(s):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def _resolve_binding(name, binding):
    return binding.get(name, name)


def _get_var(vmap, key):
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
    if val is None:
        return 0
    try:
        f = float(val)
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return 0
