"""
Structured Constraint Builder
=============================
구조화된 제약 JSON (lhs/operator/rhs)을 솔버별 제약으로 변환.

3단계 Fallback:
  1단계: 구조화 필드 (이 모듈)
  2단계: expression -> AST 파서 (expr_evaluator.py)
  3단계: 정규식 패턴 매칭 (legacy)
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BuildContext:
    """변수맵, 파라미터맵, 세트맵을 관리하는 빌드 컨텍스트"""

    def __init__(self, var_map: Dict[str, Any], param_map: Dict[str, Any], set_map: Dict[str, List]):
        self.var_map = var_map
        self.param_map = param_map
        self.set_map = set_map

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
            # list/tuple인 경우: key를 set에서 찾아 인덱스로 변환
            if isinstance(val, (list, tuple)):
                # 1) key가 정수 인덱스이고 범위 내이면 직접 사용
                if isinstance(key, int) and 0 <= key < len(val):
                    result = val[key]
                    if isinstance(result, (int, float)):
                        return int(result) if isinstance(result, float) and result == int(result) else result
                    return result
                # 2) set_map에서 key의 위치(순서) 찾기
                for set_id, set_vals in self.set_map.items():
                    if isinstance(set_vals, (list, tuple)):
                        try:
                            idx = list(set_vals).index(key)
                            if idx < len(val):
                                result = val[idx]
                                if isinstance(result, (int, float)):
                                    return int(result) if isinstance(result, float) and result == int(result) else result
                                return result
                        except (ValueError, IndexError):
                            # 타입 변환 후 재시도 (str->int 또는 int->str)
                            try:
                                converted_key = int(key) if isinstance(key, str) else str(key)
                                idx = list(set_vals).index(converted_key)
                                if idx < len(val):
                                    result = val[idx]
                                    if isinstance(result, (int, float)):
                                        return int(result) if isinstance(result, float) and result == int(result) else result
                                    return result
                            except (ValueError, IndexError, TypeError):
                                continue
                # 3) key를 int로 변환 시도
                try:
                    int_key = int(key)
                    if 0 <= int_key < len(val):
                        result = val[int_key]
                        if isinstance(result, (int, float)):
                            return int(result) if isinstance(result, float) and result == int(result) else result
                        return result
                except (ValueError, TypeError):
                    pass
                # 4) 매핑 실패 — 배열 길이와 set 크기가 다른 경우 0 반환
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
        return 0


def parse_for_each(for_each: str, ctx: BuildContext) -> List[Dict[str, Any]]:
    """for_each 문자열을 파싱하여 인덱스 바인딩 리스트 반환"""
    if not for_each or not for_each.strip():
        return [{}]

    text = for_each.strip()
    text = re.sub(r'\bfor\b', '', text).strip()
    parts = re.split(r'\s*,\s*', text)

    loop_specs = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(\w+)\s+in\s+(\w+)', part)
        if m:
            idx_name = m.group(1)
            set_name = m.group(2)
            values = ctx.get_set(set_name)
            if not values:
                logger.warning(f"Set '{set_name}' not found or empty")
            loop_specs.append((idx_name, values))

    if not loop_specs:
        return [{}]

    result = [{}]
    for idx_name, values in loop_specs:
        new_result = []
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
        else:
            return 0
        if not index_str:
            val = ctx.get_param_scalar(name)
            if val is None:
                # scalar 실패 → binding에서 인덱스 추출하여 indexed 시도
                if binding:
                    for idx_key in binding.values():
                        indexed_val = ctx.get_param_indexed(name, idx_key)
                        if indexed_val != 0 or ctx.param_map.get(name) is not None:
                            return indexed_val
                logger.warning(f"Parameter '{name}' not found in param_map, using 0")
                return 0
            return val
        index_names = parse_index_string(index_str)
        key = resolve_index(index_names, binding)
        return ctx.get_param_indexed(name, key[0] if len(key) == 1 else key)

    # sum 노드
    if 'sum' in node:
        return eval_sum_node(node['sum'], binding, ctx)

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
) -> List[Tuple[Any, str, Any]]:
    """
    구조화된 제약 JSON -> (lhs_expr, operator, rhs_expr) 리스트.
    for_each가 있으면 바인딩별로 여러 제약 생성.
    
    반환: [(lhs, op, rhs), ...] 또는 빈 리스트 (파싱 실패 시)
    """
    lhs_node = con_def.get('lhs')
    op = con_def.get('operator', '==')
    rhs_node = con_def.get('rhs')
    for_each = con_def.get('for_each', '')

    if lhs_node is None or rhs_node is None:
        return []

    bindings = parse_for_each(for_each, ctx)
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

    # 없으면 None 반환 (fallback 필요)
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
    """LP/MIP 솔버에 제약 추가"""
    try:
        if op == '==':
            ct = solver.Constraint(float(rhs), float(rhs), name) if isinstance(rhs, (int, float)) else None
        elif op == '<=':
            ct = solver.Constraint(-solver.infinity(), float(rhs), name) if isinstance(rhs, (int, float)) else None
        elif op == '>=':
            ct = solver.Constraint(float(rhs), solver.infinity(), name) if isinstance(rhs, (int, float)) else None
        else:
            return False

        if ct is None:
            return False

        # lhs가 단순 변수인 경우
        if hasattr(lhs, 'solution_value'):
            ct.SetCoefficient(lhs, 1)
            return True

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
