"""
constraints/builtin.py — Built-in side constraint handlers
===========================================================
engine이 기본 제공하는 SP Side Constraint handler 세트.

모든 handler는 모듈 로딩 시 자동 등록됨.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from engine.compiler.sp_problem import SPConstraint
from engine.constraints.base import (
    SideConstraintHandler,
    ConstraintResult,
    SideConstraintRegistry,
)
from engine.feasibility.base import resolve_param

logger = logging.getLogger(__name__)


def _resolve_soft(config: dict, params: dict) -> tuple:
    """YAML action + penalty_weight → (is_soft, penalty_weight) 해석.
    action_param으로 고객별 override 가능."""
    action = resolve_param(config, "action", params, default="reject")
    is_soft = (action == "penalize")
    weight = resolve_param(config, "penalty_weight", params, default=1.0)
    return is_soft, float(weight)


# ── cardinality: 속성 조건 column 수 제약 ───────────────────

class CardinalityConstraint(SideConstraintHandler):
    """특정 속성을 가진 column이 최소/최대 N개 선택되어야 하는 제약.

    "13시 이전 종료 + 2시간 여유 있는 duty가 10개 이상" (#16 교육시간) 등.

    YAML 예시:
      - type: cardinality
        column_attribute: training_eligible
        operator: ">="
        value: 10
        action: reject
        constraint_ref: post_shift_training

    column_attribute:
      - column의 속성 이름 (FeasibleColumn 필드 또는 동적 태깅된 속성)
      - boolean 속성: True인 column만 대상
      - numeric 속성 + threshold: threshold_param/threshold로 필터링
    """

    def build(self, columns: List[Any], params: Dict[str, Any],
              config: Dict[str, Any]) -> Optional[ConstraintResult]:
        attr_name = config.get("column_attribute", "")
        if not attr_name:
            return None

        operator = config.get("operator", ">=")
        value = resolve_param(config, "value", params)
        if value is None:
            return None
        value = float(value)

        # threshold가 있으면 numeric 비교, 없으면 boolean truthy
        threshold = resolve_param(config, "threshold", params)

        # 조건 만족 column 필터링
        eligible_ids = []
        for col in columns:
            attr_val = getattr(col, attr_name, None)
            if attr_val is None:
                continue
            if threshold is not None:
                # numeric: attr_val <= threshold (또는 >= 등)
                threshold_op = config.get("threshold_operator", "<=")
                threshold_val = float(threshold)
                if threshold_op == "<=" and float(attr_val) <= threshold_val:
                    eligible_ids.append(col.id)
                elif threshold_op == ">=" and float(attr_val) >= threshold_val:
                    eligible_ids.append(col.id)
                elif threshold_op == "<" and float(attr_val) < threshold_val:
                    eligible_ids.append(col.id)
                elif threshold_op == ">" and float(attr_val) > threshold_val:
                    eligible_ids.append(col.id)
            else:
                # boolean: truthy 판정
                if attr_val:
                    eligible_ids.append(col.id)

        if not eligible_ids:
            logger.warning(
                f"Cardinality '{attr_name}': no eligible columns found "
                f"(total={len(columns)})"
            )
            return None

        is_soft, penalty_weight = _resolve_soft(config, params)

        constraint = SPConstraint(
            name=f"cardinality_{attr_name}",
            column_ids=eligible_ids,
            operator=operator,
            rhs=value,
            label=f"Cardinality: {attr_name} {operator} {value} ({len(eligible_ids)} eligible)",
            constraint_ref=config.get("constraint_ref", ""),
            is_soft=is_soft,
            penalty_weight=penalty_weight,
        )

        return ConstraintResult(
            constraint=constraint,
            description=f"{attr_name} {operator} {value} ({len(eligible_ids)} eligible, {'soft' if is_soft else 'hard'})",
        )


# ── aggregate_avg: 선택 column 평균 제약 ────────────────────

class AggregateAvgConstraint(SideConstraintHandler):
    """선택된 column의 특정 필드 평균에 상하한을 부여하는 제약.

    "사업평균 운전시간 5시간 이내" (#2) 등.

    수학적 표현:
      Σ(field[k] * z[k]) / Σ(z[k]) <= value
      선형화: Σ(field[k] * z[k]) <= value * Σ(z[k])
      → Σ((field[k] - value) * z[k]) <= 0

    YAML 예시:
      - type: aggregate_avg
        column_field: active_minutes
        operator: "<="
        value_param: avg_driving_target_minutes
        action: penalize
        penalty_weight: 10
        constraint_ref: avg_driving_time_target
    """

    def build(self, columns: List[Any], params: Dict[str, Any],
              config: Dict[str, Any]) -> Optional[ConstraintResult]:
        field_name = config.get("column_field", "")
        if not field_name:
            return None

        operator = config.get("operator", "<=")
        value = resolve_param(config, "value", params)
        if value is None:
            return None
        value = float(value)

        # 선형화: Σ((field[k] - value) * z[k]) op 0
        # coefficients = {col_id: field_value - value}
        coefficients = {}
        column_ids = []
        for col in columns:
            field_val = getattr(col, field_name, None)
            if field_val is None:
                continue
            coeff = float(field_val) - value
            coefficients[col.id] = coeff
            column_ids.append(col.id)

        if not column_ids:
            logger.warning(
                f"AggregateAvg '{field_name}': no columns have field "
                f"(total={len(columns)})"
            )
            return None

        is_soft, penalty_weight = _resolve_soft(config, params)

        constraint = SPConstraint(
            name=f"aggregate_avg_{field_name}",
            column_ids=column_ids,
            operator=operator,
            rhs=0.0,  # 선형화 후 rhs = 0
            label=(
                f"AggregateAvg: avg({field_name}) {operator} {value} "
                f"({len(column_ids)} columns, {'soft' if is_soft else 'hard'})"
            ),
            coefficients=coefficients,
            constraint_ref=config.get("constraint_ref", ""),
            is_soft=is_soft,
            penalty_weight=penalty_weight,
        )

        return ConstraintResult(
            constraint=constraint,
            description=f"avg({field_name}) {operator} {value} ({len(column_ids)} columns, {'soft' if is_soft else 'hard'})",
        )


# ── aggregate_sum: 선택 column 합계 제약 ────────────────────

class AggregateSumConstraint(SideConstraintHandler):
    """선택된 column의 특정 필드 합계에 상하한을 부여하는 제약.

    YAML 예시:
      - type: aggregate_sum
        column_field: active_minutes
        operator: "<="
        value_param: total_driving_budget
        constraint_ref: total_driving_limit
    """

    def build(self, columns: List[Any], params: Dict[str, Any],
              config: Dict[str, Any]) -> Optional[ConstraintResult]:
        field_name = config.get("column_field", "")
        if not field_name:
            return None

        operator = config.get("operator", "<=")
        value = resolve_param(config, "value", params)
        if value is None:
            return None
        value = float(value)

        coefficients = {}
        column_ids = []
        for col in columns:
            field_val = getattr(col, field_name, None)
            if field_val is None:
                continue
            coefficients[col.id] = float(field_val)
            column_ids.append(col.id)

        if not column_ids:
            return None

        is_soft, penalty_weight = _resolve_soft(config, params)

        constraint = SPConstraint(
            name=f"aggregate_sum_{field_name}",
            column_ids=column_ids,
            operator=operator,
            rhs=value,
            label=f"AggregateSum: sum({field_name}) {operator} {value} ({len(column_ids)} columns, {'soft' if is_soft else 'hard'})",
            coefficients=coefficients,
            constraint_ref=config.get("constraint_ref", ""),
            is_soft=is_soft,
            penalty_weight=penalty_weight,
        )

        return ConstraintResult(
            constraint=constraint,
            description=f"sum({field_name}) {operator} {value} ({len(column_ids)} columns)",
        )


# ── 자동 등록 ───────────────────────────────────────────────

def register_builtin_handlers():
    """built-in handler를 registry에 등록."""
    SideConstraintRegistry.register("cardinality", CardinalityConstraint)
    SideConstraintRegistry.register("aggregate_avg", AggregateAvgConstraint)
    SideConstraintRegistry.register("aggregate_sum", AggregateSumConstraint)


# 모듈 로딩 시 자동 등록
register_builtin_handlers()
