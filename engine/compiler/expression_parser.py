import re
import logging

logger = logging.getLogger(__name__)


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

    bindings = _parse_for_each(for_each_str, ctx)
    count = 0
    for binding in bindings:
        try:
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
        return left * right

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
