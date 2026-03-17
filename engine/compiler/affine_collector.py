"""
engine/compiler/affine_collector.py
───────────────────────────────────
Affine Expression Collector — 컴파일러 성능 최적화의 핵심.

expression_template 문자열을 AST로 파싱한 뒤,
affine subset에 속하면 AffineExprIR로 직접 수집하여
expression_parser의 문자열 반복 파싱을 우회한다.

핵심 함수:
  parse_expression_to_ast() — 수식 문자열 → AST dict
  is_affine_supported()    — AST가 affine subset에 속하는지 검사
  eval_scalar()            — scalar 평가 (variable 포함 시 예외)
  collect_affine()         — AffineExprIR로 수집
  normalize_constraint()   — zero-RHS 정규형 (lhs-rhs op 0)
  check_constant_constraint() — constant-only 제약 판정
  lower_affine_to_dimod()  — AffineExprIR → dimod expression
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.compiler.errors import (
    UnsupportedStructuredPattern,
    NonScalarBoundValueError,
    NoneValueError,
    NonFiniteValueError,
    VariableResolutionError,
)
from engine.compiler.struct_builder import coerce_scalar, normalize_index_key

logger = logging.getLogger(__name__)


# ============================================================
# IR Types
# ============================================================

@dataclass(frozen=True)
class VarRef:
    """변수 참조 — hashable canonical form."""
    name: str
    indices: tuple = ()

    def __str__(self):
        if self.indices:
            return f"{self.name}[{','.join(str(i) for i in self.indices)}]"
        return self.name


@dataclass
class AffineExprIR:
    """Affine expression: constant + Σ(coeff_k * var_k)"""
    constant: float = 0.0
    linear_terms: Dict[VarRef, float] = field(default_factory=dict)

    def prune_near_zero(self, eps: float = 1e-12):
        self.linear_terms = {k: v for k, v in self.linear_terms.items() if abs(v) >= eps}

    @property
    def is_constant_only(self) -> bool:
        return len(self.linear_terms) == 0

    def __repr__(self):
        parts = []
        if self.constant != 0:
            parts.append(str(self.constant))
        for vr, c in self.linear_terms.items():
            parts.append(f"{c}*{vr}")
        return " + ".join(parts) if parts else "0"


def add_affine(a: AffineExprIR, b: AffineExprIR) -> AffineExprIR:
    result = AffineExprIR(constant=a.constant + b.constant, linear_terms=dict(a.linear_terms))
    for vr, coeff in b.linear_terms.items():
        result.linear_terms[vr] = result.linear_terms.get(vr, 0.0) + coeff
    return result


def scale_affine(a: AffineExprIR, s: float) -> AffineExprIR:
    return AffineExprIR(
        constant=a.constant * s,
        linear_terms={vr: c * s for vr, c in a.linear_terms.items()},
    )


# ============================================================
# AST Parser — expression_template 문자열 → AST dict
# ============================================================

# 사전 컴파일 정규식
_RE_SUM = re.compile(r'^sum\((.+)\)$', re.DOTALL)
_RE_VAR_IDX = re.compile(r'^(\w+)\[([^\]]+)\]$')
_RE_NUMBER = re.compile(r'^-?\d+(\.\d+)?$')
_RE_FOR = re.compile(r'(\w+)\s+in\s+(\w+)')


def parse_expression_to_ast(expr_str: str) -> dict:
    """수식 문자열을 AST dict로 파싱."""
    expr_str = expr_str.strip()

    # 바깥 괄호 제거
    while expr_str.startswith('(') and _matching_paren(expr_str) == len(expr_str) - 1:
        expr_str = expr_str[1:-1].strip()

    # sum(...) 패턴
    m_sum = _RE_SUM.match(expr_str)
    if m_sum:
        inner = m_sum.group(1)
        return _parse_sum(inner)

    # 덧셈/뺄셈 (최상위 레벨)
    split = _find_top_level_addop(expr_str)
    if split is not None:
        pos, op_char = split
        left = parse_expression_to_ast(expr_str[:pos].strip())
        right = parse_expression_to_ast(expr_str[pos + 1:].strip())
        if op_char == '-':
            return {"type": "subtract", "terms": [left, right]}
        return {"type": "add", "terms": [left, right]}

    # 곱셈 (최상위 레벨)
    mul_pos = _find_top_level_mul(expr_str)
    if mul_pos is not None:
        left = parse_expression_to_ast(expr_str[:mul_pos].strip())
        right = parse_expression_to_ast(expr_str[mul_pos + 1:].strip())
        return {"type": "product", "terms": [left, right]}

    # 숫자 리터럴
    if _RE_NUMBER.match(expr_str):
        val = float(expr_str)
        if val == int(val):
            val = int(val)
        return {"type": "constant", "value": val}

    # 변수/파라미터 인덱스 접근: name[idx,...]
    m_var = _RE_VAR_IDX.match(expr_str)
    if m_var:
        name = m_var.group(1)
        indices = tuple(p.strip() for p in m_var.group(2).split(','))
        return {"type": "indexed", "name": name, "indices": indices}

    # 단순 식별자
    if re.match(r'^\w+$', expr_str):
        return {"type": "identifier", "name": expr_str}

    # 파싱 불가
    return {"type": "unknown", "raw": expr_str}


def _parse_sum(inner: str) -> dict:
    """sum(body for var in Set) 파싱."""
    for_idx = inner.rfind(' for ')
    if for_idx < 0:
        return {"type": "unknown", "raw": f"sum({inner})"}
    body_str = inner[:for_idx].strip()
    iter_str = inner[for_idx + 5:].strip()
    m = _RE_FOR.match(iter_str)
    if not m:
        return {"type": "unknown", "raw": f"sum({inner})"}
    return {
        "type": "sum_over",
        "body": parse_expression_to_ast(body_str),
        "iter_var": m.group(1),
        "set_name": m.group(2),
    }


def _matching_paren(s: str) -> int:
    """첫 '('에 매칭되는 ')'의 위치 반환."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def _find_top_level_addop(expr: str) -> Optional[Tuple[int, str]]:
    """최상위 레벨(paren=0)의 마지막 +/- 위치 반환."""
    paren = 0
    last_pos = None
    last_op = None
    for i, ch in enumerate(expr):
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        elif paren == 0 and ch in '+-' and i > 0:
            prev = expr[i - 1]
            if prev not in '*+-/(':
                last_pos = i
                last_op = ch
    return (last_pos, last_op) if last_pos is not None else None


def _find_top_level_mul(expr: str) -> Optional[int]:
    """최상위 레벨의 마지막 * 위치 반환."""
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


# ============================================================
# AST Affine Support Check
# ============================================================

def is_affine_supported(ast: dict) -> bool:
    """AST가 affine subset에 속하는지 재귀 검사."""
    t = ast.get("type")
    if t in ("constant", "identifier", "indexed"):
        return True
    if t in ("add", "subtract"):
        return all(is_affine_supported(term) for term in ast.get("terms", []))
    if t == "product":
        terms = ast.get("terms", [])
        if len(terms) != 2:
            return False
        # 양쪽 중 하나는 반드시 scalar(constant/identifier/indexed)이어야 함
        # variable×variable은 불허 — 하지만 정적으로 variable인지 알 수 없으므로
        # 보수적으로 product는 허용하고, collect_affine 시 runtime에 판단
        return all(is_affine_supported(term) for term in terms)
    if t == "sum_over":
        return is_affine_supported(ast.get("body", {}))
    if t == "unknown":
        return False
    return False


@dataclass
class ParsedExprCacheEntry:
    ast_lhs: dict
    ast_rhs: dict
    op: str
    affine_supported: bool


_expr_cache: Dict[str, ParsedExprCacheEntry] = {}


def parse_constraint_expr_cached(expr_str: str) -> Optional[ParsedExprCacheEntry]:
    """제약식 문자열을 파싱하고 캐시. op로 lhs/rhs 분리."""
    if expr_str in _expr_cache:
        return _expr_cache[expr_str]

    for op_try in ['<=', '>=', '==']:
        if op_try in expr_str:
            parts = expr_str.split(op_try, 1)
            ast_lhs = parse_expression_to_ast(parts[0].strip())
            ast_rhs = parse_expression_to_ast(parts[1].strip())
            supported = is_affine_supported(ast_lhs) and is_affine_supported(ast_rhs)
            entry = ParsedExprCacheEntry(
                ast_lhs=ast_lhs, ast_rhs=ast_rhs, op=op_try,
                affine_supported=supported,
            )
            _expr_cache[expr_str] = entry
            return entry

    return None


# ============================================================
# eval_scalar — scalar 전용 평가 (variable 포함 시 예외)
# ============================================================

def eval_scalar(ast: dict, binding: Dict[str, Any], ctx: Any) -> float:
    """
    AST 노드를 scalar float으로 평가.
    variable이 포함되면 UnsupportedStructuredPattern.
    """
    t = ast.get("type")

    if t == "constant":
        return float(coerce_scalar(ast["value"], name="constant"))

    if t == "identifier":
        name = ast["name"]
        # binding에서 먼저 찾기
        if name in binding:
            v = binding[name]
            return float(coerce_scalar(v, name=name))
        # parameter에서 찾기
        val = ctx.get_param_scalar(name)
        if val is not None:
            return float(coerce_scalar(val, name=name))
        raise NoneValueError(f"scalar '{name}' not found")

    if t == "indexed":
        name = ast["name"]
        indices = tuple(_resolve_binding(idx, binding) for idx in ast["indices"])
        key = indices[0] if len(indices) == 1 else indices
        # 변수인지 파라미터인지 확인
        if name in getattr(ctx, 'var_map', {}):
            raise UnsupportedStructuredPattern(f"variable '{name}' in scalar context")
        val = ctx.get_param_indexed(name, key)
        if val == 0 and name not in getattr(ctx, 'param_map', {}):
            raise NoneValueError(f"param '{name}[{key}]' not found")
        return float(coerce_scalar(val, name=f"{name}[{key}]"))

    if t in ("add", "subtract"):
        terms = ast.get("terms", [])
        if len(terms) < 2:
            raise UnsupportedStructuredPattern(f"malformed {t}")
        a = eval_scalar(terms[0], binding, ctx)
        b = eval_scalar(terms[1], binding, ctx)
        return a + b if t == "add" else a - b

    if t == "product":
        terms = ast.get("terms", [])
        if len(terms) < 2:
            raise UnsupportedStructuredPattern(f"malformed product")
        a = eval_scalar(terms[0], binding, ctx)
        b = eval_scalar(terms[1], binding, ctx)
        return a * b

    raise UnsupportedStructuredPattern(f"eval_scalar: unsupported type '{t}'")


# ============================================================
# collect_affine — AffineExprIR 수집
# ============================================================

def collect_affine(ast: dict, binding: Dict[str, Any], ctx: Any) -> AffineExprIR:
    """
    AST 노드를 AffineExprIR로 수집.

    지원: constant, variable, parameter, add, subtract,
          scalar*variable, scalar*affine, sum_over
    미지원: variable×variable → UnsupportedStructuredPattern
    """
    t = ast.get("type")

    if t == "constant":
        return AffineExprIR(constant=float(coerce_scalar(ast["value"], name="constant")))

    if t == "identifier":
        name = ast["name"]
        # binding에서 찾기
        if name in binding:
            v = binding[name]
            return AffineExprIR(constant=float(coerce_scalar(v, name=name)))
        # parameter인지 확인
        val = ctx.get_param_scalar(name)
        if val is not None:
            return AffineExprIR(constant=float(coerce_scalar(val, name=name)))
        # variable인지 확인 (indexed가 아닌 bare variable은 드묾)
        raise NoneValueError(f"identifier '{name}' not found")

    if t == "indexed":
        name = ast["name"]
        indices = tuple(_resolve_binding(idx, binding) for idx in ast["indices"])
        # 항상 tuple로 유지 (single-index도)
        key = normalize_index_key(indices)

        # 변수인지 파라미터인지 판별
        var_map = getattr(ctx, 'var_map', {})
        if name in var_map and isinstance(var_map[name], dict):
            var = ctx.get_var(name, key)
            if var == 0:
                # single-index로도 시도
                if len(indices) == 1:
                    var = ctx.get_var(name, normalize_index_key(indices[0]))
                if var == 0:
                    return AffineExprIR(constant=0.0)
            vr = VarRef(name=name, indices=key if isinstance(key, tuple) else (key,))
            return AffineExprIR(linear_terms={vr: 1.0})

        # 파라미터
        val = ctx.get_param_indexed(name, key)
        return AffineExprIR(constant=float(coerce_scalar(val, name=f"{name}[{key}]")))

    if t == "add":
        terms = ast.get("terms", [])
        result = AffineExprIR()
        for term in terms:
            result = add_affine(result, collect_affine(term, binding, ctx))
        return result

    if t == "subtract":
        terms = ast.get("terms", [])
        if len(terms) < 2:
            raise UnsupportedStructuredPattern("malformed subtract")
        a = collect_affine(terms[0], binding, ctx)
        b = collect_affine(terms[1], binding, ctx)
        return add_affine(a, scale_affine(b, -1.0))

    if t == "product":
        terms = ast.get("terms", [])
        if len(terms) < 2:
            raise UnsupportedStructuredPattern("malformed product")
        # 어느 쪽이 scalar인지 시도
        left_scalar = _try_eval_as_scalar(terms[0], binding, ctx)
        right_scalar = _try_eval_as_scalar(terms[1], binding, ctx)

        if left_scalar is not None and right_scalar is not None:
            # 양쪽 모두 scalar
            return AffineExprIR(constant=left_scalar * right_scalar)
        elif left_scalar is not None:
            # scalar × affine
            right_affine = collect_affine(terms[1], binding, ctx)
            return scale_affine(right_affine, left_scalar)
        elif right_scalar is not None:
            # affine × scalar
            left_affine = collect_affine(terms[0], binding, ctx)
            return scale_affine(left_affine, right_scalar)
        else:
            # affine × affine → 미지원
            raise UnsupportedStructuredPattern("variable × variable not supported in affine context")

    if t == "sum_over":
        body = ast.get("body", {})
        iter_var = ast.get("iter_var", "")
        set_name = ast.get("set_name", "")
        set_vals = ctx.get_set(set_name) if hasattr(ctx, 'get_set') else ctx.set_map.get(set_name, [])

        if not set_vals:
            return AffineExprIR()  # 빈 합 = 0

        ir = AffineExprIR()
        sentinel = object()
        old = binding.get(iter_var, sentinel)
        try:
            for val in set_vals:
                binding[iter_var] = val
                term_ir = collect_affine(body, binding, ctx)
                ir = add_affine(ir, term_ir)
        finally:
            if old is sentinel:
                binding.pop(iter_var, None)
            else:
                binding[iter_var] = old

        ir.prune_near_zero()
        return ir

    raise UnsupportedStructuredPattern(f"collect_affine: unsupported type '{t}'")


def _try_eval_as_scalar(ast: dict, binding: Dict, ctx: Any) -> Optional[float]:
    """scalar로 평가 시도. 실패(variable 포함)하면 None 반환."""
    try:
        return eval_scalar(ast, binding, ctx)
    except (UnsupportedStructuredPattern, NoneValueError):
        return None


def _resolve_binding(idx_str: str, binding: Dict[str, Any]) -> Any:
    """인덱스 문자열을 바인딩에서 해석."""
    idx_str = idx_str.strip()
    if idx_str in binding:
        return binding[idx_str]
    # 숫자 리터럴
    try:
        v = int(idx_str)
        return v
    except ValueError:
        try:
            return float(idx_str)
        except ValueError:
            return idx_str


# ============================================================
# Constraint Normalization + Constant Check
# ============================================================

def normalize_constraint(
    lhs: AffineExprIR, op: str, rhs: AffineExprIR,
) -> Tuple[AffineExprIR, str, float]:
    """lhs op rhs → (lhs - rhs) op 0 (zero-RHS 정규형)"""
    diff = add_affine(lhs, scale_affine(rhs, -1.0))
    diff.prune_near_zero()
    return (diff, op, 0.0)


def check_constant_constraint(expr: AffineExprIR, op: str) -> str:
    """
    변수가 없는 순수 상수 제약 판정.
    Returns: "normal" | "tautology" | "infeasible"
    """
    if not expr.is_constant_only:
        return "normal"
    c = expr.constant
    if op == "<=" and c <= 1e-12:
        return "tautology"
    if op == ">=" and c >= -1e-12:
        return "tautology"
    if op == "==" and abs(c) < 1e-12:
        return "tautology"
    return "infeasible"


# ============================================================
# Lowering: AffineExprIR → dimod CQM expression
# ============================================================

def lower_affine_to_dimod(
    expr: AffineExprIR,
    var_lookup: Dict[VarRef, Any],
) -> Any:
    """AffineExprIR → dimod expression. 결정적 순서 보장."""
    try:
        import dimod
    except ImportError:
        raise ImportError("dimod is required for CQM lowering")

    terms = []
    for vr in sorted(expr.linear_terms, key=lambda v: (v.name, v.indices)):
        coeff = expr.linear_terms[vr]
        if abs(coeff) < 1e-12:
            continue
        dvar = var_lookup.get(vr)
        if dvar is None:
            # str↔int 키 불일치 fallback
            alt_indices = tuple(str(i) for i in vr.indices)
            alt_vr = VarRef(name=vr.name, indices=alt_indices)
            dvar = var_lookup.get(alt_vr)
        if dvar is None:
            # int 변환 시도
            try:
                int_indices = tuple(int(i) for i in vr.indices)
                int_vr = VarRef(name=vr.name, indices=int_indices)
                dvar = var_lookup.get(int_vr)
            except (ValueError, TypeError):
                pass
        if dvar is None:
            raise VariableResolutionError(f"VarRef {vr} not found in var_lookup")
        terms.append(float(coeff) * dvar)

    result = dimod.quicksum(terms) if terms else 0
    if abs(expr.constant) >= 1e-12:
        result = result + expr.constant
    return result


def build_var_lookup(var_map: dict) -> Dict[VarRef, Any]:
    """var_map (name → {key → dimod_var}) → VarRef → dimod_var 매핑.

    바인딩에서 올 수 있는 키 형태(int, str)를 모두 등록하여
    VarRef 매칭 실패를 방지한다.
    """
    lookup: Dict[VarRef, Any] = {}
    for name, vmap in var_map.items():
        if not isinstance(vmap, dict):
            continue
        for key, dvar in vmap.items():
            if isinstance(key, tuple):
                indices = tuple(normalize_index_key(key))
            else:
                indices = (normalize_index_key(key),) if key is not None else ()

            # 원본 키로 등록
            vr = VarRef(name=name, indices=indices)
            lookup[vr] = dvar

            # 문자열 변환 키도 등록 (바인딩이 str로 올 수 있음)
            str_indices = tuple(str(i) for i in indices)
            if str_indices != indices:
                vr_str = VarRef(name=name, indices=str_indices)
                lookup[vr_str] = dvar
    return lookup
