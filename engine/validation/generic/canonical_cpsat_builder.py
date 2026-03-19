"""
canonical_cpsat_builder.py ──────────────────────────────────
Canonical IR → CP-SAT 변환기 (Presolve 전용).

Presolve Soundness Policy:
  - Canonical IR(L4 출력) 기준으로만 CP-SAT 모델 생성
  - D-Wave/기타 solver-specific 변환은 절대 포함하지 않음
  - 변환 불가 제약(non-linear 등)은 relaxed 또는 dropped → 리포트 기록
  - FEASIBLE in presolve ≠ guaranteed feasible in solver

이 모듈은 실제 solver 실행 경로(ortools_compiler)와 독립적으로 동작하며,
기존 컴파일러의 struct_builder/expression_parser를 재사용하여 일관성 유지.
"""

from __future__ import annotations

import logging
import time
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from engine.validation.generic.presolve_models import (
    CPSATBuildReport,
    DroppedImpactLevel,
)

logger = logging.getLogger(__name__)


def build_cpsat_for_presolve(
    math_model: Dict,
    bound_data: Dict,
) -> Tuple[Any, CPSATBuildReport]:
    """
    Canonical IR → CP-SAT 모델 변환 (presolve feasibility 검사 전용).

    기존 ortools_compiler의 핵심 로직을 재사용하되:
      - solver-specific 최적화/변환 없음
      - soft constraint → hard로 변환 (feasibility만 판정)
      - 변환 불가 제약은 drop + 리포트 기록

    Args:
        math_model: 수학 모델 IR (variables, constraints, objective, sets, parameters)
        bound_data: DataBinder.bind_all() 결과

    Returns:
        (CpModel, CPSATBuildReport) 튜플
    """
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    report = CPSATBuildReport()

    # ── 1. 변수 생성 ──
    var_map: Dict[str, Any] = {}
    total_vars = 0

    for var_def in math_model.get("variables", []):
        vid = var_def.get("id", "")
        vtype = _normalize_var_type(var_def.get("type", "binary"))
        indices = var_def.get("indices", [])

        if not indices:
            var_map[vid] = _create_var(model, vid, vtype, var_def)
            total_vars += 1
        else:
            combos = _compute_set_product(indices, bound_data)
            var_map[vid] = {}
            for combo in combos:
                key = tuple(str(c) for c in combo)
                name = f"{vid}_{'_'.join(key)}"
                var_map[vid][key] = _create_var(model, name, vtype, var_def)
                total_vars += 1

    report.variable_count = total_vars

    # ── 2. 제약 생성 (canonical 기준, solver-specific 변환 없음) ──
    param_map = bound_data.get("parameters", {})
    set_map = bound_data.get("sets", {})

    supported = []
    relaxed = []
    dropped = []
    constraint_name_map: Dict[int, str] = {}
    total_constraints = 0

    try:
        from engine.compiler.struct_builder import (
            BuildContext, build_constraint, apply_constraint_cpsat,
        )
        from engine.compiler.expression_parser import parse_and_apply_expression
        has_builders = True
    except ImportError:
        has_builders = False
        logger.warning("L5:presolve:builder_import_failed — struct_builder 미사용")

    ctx = None
    if has_builders:
        ctx = BuildContext(var_map, param_map, set_map, model=model)

    for con_def in math_model.get("constraints", []):
        cname = con_def.get("name", con_def.get("id", "unknown"))
        category = con_def.get("category", con_def.get("priority", "hard"))
        expr_str = con_def.get("expression", "").strip()

        # Presolve에서는 soft도 hard로 취급 (feasibility만 판정)
        count = 0

        if not has_builders:
            dropped.append(cname)
            continue

        # 방법 1: expression_parser
        if expr_str and any(op in expr_str for op in ["<=", ">=", "=="]):
            try:
                for_each_str = con_def.get("for_each", "")
                count = parse_and_apply_expression(
                    model, expr_str, for_each_str, ctx, var_map
                )
            except Exception:
                count = 0

        # 방법 2: structured (lhs/rhs)
        if count == 0:
            has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None
            if has_struct:
                try:
                    results = build_constraint(con_def, ctx)
                    for lhs_val, op, rhs_val in results:
                        if apply_constraint_cpsat(model, lhs_val, op, rhs_val):
                            count += 1
                except Exception:
                    count = 0

        # 결과 분류
        if count > 0:
            supported.append(cname)
            for i in range(count):
                constraint_name_map[total_constraints + i] = cname
            total_constraints += count
        else:
            # 변환 불가 → dropped
            dropped.append(cname)
            logger.info(
                f"L5:presolve:dropped_constraint name={cname} "
                f"category={category} reason=build_failed"
            )

    report.supported_constraints = supported
    report.relaxed_constraints = relaxed
    report.dropped_constraints = dropped
    report.constraint_name_map = constraint_name_map
    report.constraint_count = total_constraints

    # ── 3. Fidelity Score 계산 ──
    total_defined = len(math_model.get("constraints", []))
    if total_defined > 0:
        report.fidelity_score = len(supported) / total_defined
    else:
        report.fidelity_score = 1.0

    report.fidelity_note = (
        f"{len(supported)}개 변환 완료, "
        f"{len(relaxed)}개 근사, "
        f"{len(dropped)}개 생략 "
        f"(총 {total_defined}개 중)"
    )

    # ── 4. Dropped Impact Level 평가 ──
    report.dropped_impact_level, report.dropped_impact_note = _assess_dropped_impact(
        dropped, math_model.get("constraints", [])
    )

    return model, report


# ── Helper 함수 ────────────────────────────────────────────

def _normalize_var_type(vtype: str) -> str:
    """IR 변수 타입을 정규화"""
    vtype = vtype.lower().strip()
    aliases = {
        "numeric": "integer",  # presolve에서는 integer로 통일
        "float": "integer",
        "real": "integer",
        "continuous": "integer",
        "bool": "binary",
        "boolean": "binary",
        "int": "integer",
    }
    return aliases.get(vtype, vtype)


def _create_var(model: Any, name: str, vtype: str, var_def: Dict) -> Any:
    """CP-SAT 변수 하나 생성"""
    if vtype == "binary":
        return model.new_bool_var(name)
    else:
        lb = int(var_def.get("lower_bound") or 0)
        ub = int(var_def.get("upper_bound") or 1_000_000)
        return model.new_int_var(lb, ub, name)


def _compute_set_product(indices: List[str], bound_data: Dict) -> List[tuple]:
    """인덱스 집합의 데카르트 곱 계산"""
    sets_values = []
    for idx in indices:
        values = bound_data.get("sets", {}).get(idx, [])
        if not values:
            return []
        sets_values.append(values)
    return list(product(*sets_values))


def _assess_dropped_impact(
    dropped: List[str],
    constraint_defs: List[Dict],
) -> Tuple[DroppedImpactLevel, str]:
    """
    생략된 제약의 영향도를 평가.

    판단 기준:
      - hard / capacity / coverage 카테고리 → HIGH
      - 5개 이상 생략 → MEDIUM
      - 그 외 → LOW
    """
    if not dropped:
        return DroppedImpactLevel.LOW, ""

    dropped_set = set(dropped)
    high_impact_categories = {"hard", "capacity", "coverage"}
    high_count = 0

    for cdef in constraint_defs:
        cname = cdef.get("name", cdef.get("id", ""))
        if cname in dropped_set:
            cat = cdef.get("category", cdef.get("priority", "hard"))
            if cat in high_impact_categories:
                high_count += 1

    if high_count > 0:
        return (
            DroppedImpactLevel.HIGH,
            f"{high_count}개의 핵심 제약(hard/capacity/coverage)이 "
            f"presolve에서 제외되어 결과 신뢰도가 낮습니다.",
        )
    elif len(dropped) >= 5:
        return (
            DroppedImpactLevel.MEDIUM,
            f"{len(dropped)}개 제약이 presolve에서 제외되었습니다.",
        )
    else:
        return (
            DroppedImpactLevel.LOW,
            f"{len(dropped)}개 제약이 제외되었으나 영향도가 낮습니다.",
        )
