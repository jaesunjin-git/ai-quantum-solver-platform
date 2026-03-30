"""
SP Side Constraint Pipeline 테스트
===================================
Phase 2a~2c 검증:
  - SideConstraintHandler 인터페이스 + Registry + Pipeline
  - SPConstraint coefficient 확장
  - built-in handler: cardinality, aggregate_avg, aggregate_sum
  - YAML 로딩 + sp_problem 연동
"""

import pytest
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ── 테스트용 mock ───────────────────────────────────────────

@dataclass
class MockColumn:
    """FeasibleColumn 대용"""
    id: int = 1
    trips: List[int] = None
    active_minutes: int = 0
    idle_minutes: int = 0
    elapsed_minutes: int = 0
    span_minutes: int = 0
    start_time: int = 0
    end_time: int = 0
    column_type: str = "day"
    cost: float = 1.0
    source: str = "beam"
    # 동적 속성 태깅용
    training_eligible: bool = False
    short_duty: bool = False

    def __post_init__(self):
        if self.trips is None:
            self.trips = [self.id]


def _make_columns(specs: List[dict]) -> List[MockColumn]:
    """[{id, active_minutes, ...}, ...] → MockColumn 목록"""
    return [MockColumn(**{**{"id": i + 1}, **s}) for i, s in enumerate(specs)]


# ============================================================
# Registry 테스트
# ============================================================

class TestSideConstraintRegistry:

    def test_builtin_handlers_registered(self):
        from engine.constraints.builtin import register_builtin_handlers  # noqa
        from engine.constraints.base import SideConstraintRegistry
        for name in ["cardinality", "aggregate_avg", "aggregate_sum"]:
            assert SideConstraintRegistry.get(name) is not None, f"{name} not registered"

    def test_unknown_type_returns_none(self):
        from engine.constraints.base import SideConstraintRegistry
        assert SideConstraintRegistry.get("nonexistent") is None


# ============================================================
# CardinalityConstraint 테스트
# ============================================================

class TestCardinalityConstraint:

    def test_basic_boolean_filter(self):
        """boolean 속성으로 column 필터링"""
        from engine.constraints.builtin import CardinalityConstraint
        handler = CardinalityConstraint()

        cols = _make_columns([
            {"training_eligible": True},
            {"training_eligible": True},
            {"training_eligible": False},
            {"training_eligible": True},
        ])

        config = {"column_attribute": "training_eligible", "operator": ">=", "value": 2}
        result = handler.build(cols, {}, config)

        assert result is not None
        assert len(result.constraint.column_ids) == 3  # True인 것만
        assert result.constraint.operator == ">="
        assert result.constraint.rhs == 2

    def test_threshold_filter(self):
        """numeric 속성 + threshold로 필터링"""
        from engine.constraints.builtin import CardinalityConstraint
        handler = CardinalityConstraint()

        cols = _make_columns([
            {"end_time": 750},   # 12:30 — 13시 이전
            {"end_time": 800},   # 13:20 — 13시 이후
            {"end_time": 720},   # 12:00 — 13시 이전
        ])

        config = {
            "column_attribute": "end_time",
            "threshold": 780,              # 13:00
            "threshold_operator": "<=",
            "operator": ">=",
            "value": 2,
        }
        result = handler.build(cols, {}, config)

        assert result is not None
        assert len(result.constraint.column_ids) == 2  # 750, 720

    def test_no_eligible_returns_none(self):
        """조건 만족 column이 없으면 None"""
        from engine.constraints.builtin import CardinalityConstraint
        handler = CardinalityConstraint()

        cols = _make_columns([{"training_eligible": False}])
        config = {"column_attribute": "training_eligible", "operator": ">=", "value": 1}
        result = handler.build(cols, {}, config)
        assert result is None

    def test_param_reference(self):
        """value_param으로 params에서 값 조회"""
        from engine.constraints.builtin import CardinalityConstraint
        handler = CardinalityConstraint()

        cols = _make_columns([{"training_eligible": True}] * 5)
        config = {
            "column_attribute": "training_eligible",
            "operator": ">=",
            "value_param": "min_training_duties",
        }
        result = handler.build(cols, {"min_training_duties": 3}, config)
        assert result is not None
        assert result.constraint.rhs == 3


# ============================================================
# AggregateAvgConstraint 테스트
# ============================================================

class TestAggregateAvgConstraint:

    def test_basic_linearization(self):
        """선형화: Σ((field-value)*z) <= 0"""
        from engine.constraints.builtin import AggregateAvgConstraint
        handler = AggregateAvgConstraint()

        cols = _make_columns([
            {"active_minutes": 350},  # coeff = 350 - 300 = 50
            {"active_minutes": 280},  # coeff = 280 - 300 = -20
            {"active_minutes": 320},  # coeff = 320 - 300 = 20
        ])

        config = {"column_field": "active_minutes", "operator": "<=", "value": 300}
        result = handler.build(cols, {}, config)

        assert result is not None
        assert result.constraint.rhs == 0.0
        assert result.constraint.operator == "<="
        # coefficients 확인
        coeffs = result.constraint.coefficients
        assert coeffs[1] == pytest.approx(50.0)
        assert coeffs[2] == pytest.approx(-20.0)
        assert coeffs[3] == pytest.approx(20.0)

    def test_param_reference(self):
        """value_param으로 params에서 limit 조회"""
        from engine.constraints.builtin import AggregateAvgConstraint
        handler = AggregateAvgConstraint()

        cols = _make_columns([{"idle_minutes": 200}, {"idle_minutes": 100}])
        config = {
            "column_field": "idle_minutes",
            "operator": "<=",
            "value_param": "avg_wait_target_minutes",
        }
        result = handler.build(cols, {"avg_wait_target_minutes": 180}, config)

        assert result is not None
        coeffs = result.constraint.coefficients
        assert coeffs[1] == pytest.approx(20.0)   # 200 - 180
        assert coeffs[2] == pytest.approx(-80.0)  # 100 - 180

    def test_missing_field_returns_none(self):
        from engine.constraints.builtin import AggregateAvgConstraint
        handler = AggregateAvgConstraint()
        cols = _make_columns([{}])
        config = {"column_field": "nonexistent", "operator": "<=", "value": 100}
        result = handler.build(cols, {}, config)
        assert result is None


# ============================================================
# AggregateSumConstraint 테스트
# ============================================================

class TestAggregateSumConstraint:

    def test_basic_sum(self):
        from engine.constraints.builtin import AggregateSumConstraint
        handler = AggregateSumConstraint()

        cols = _make_columns([
            {"active_minutes": 300},
            {"active_minutes": 250},
        ])

        config = {"column_field": "active_minutes", "operator": "<=", "value": 10000}
        result = handler.build(cols, {}, config)

        assert result is not None
        assert result.constraint.rhs == 10000
        assert result.constraint.coefficients[1] == 300
        assert result.constraint.coefficients[2] == 250


# ============================================================
# SPConstraint coefficient 테스트
# ============================================================

class TestSPConstraintCoefficients:

    def test_backward_compatible(self):
        """coefficients=None이면 기존 동작"""
        from engine.compiler.sp_problem import SPConstraint
        c = SPConstraint(name="test", column_ids=[1, 2], operator="<=", rhs=10)
        assert c.coefficients is None

    def test_with_coefficients(self):
        """coefficients가 있으면 가중합"""
        from engine.compiler.sp_problem import SPConstraint
        c = SPConstraint(
            name="avg_test",
            column_ids=[1, 2, 3],
            operator="<=",
            rhs=0.0,
            coefficients={1: 50.0, 2: -20.0, 3: 20.0},
        )
        assert c.coefficients[1] == 50.0
        assert c.rhs == 0.0

    def test_constraint_ref(self):
        """constraint_ref 추적성 필드"""
        from engine.compiler.sp_problem import SPConstraint
        c = SPConstraint(
            name="test", column_ids=[1], operator=">=", rhs=10,
            constraint_ref="post_shift_training",
        )
        assert c.constraint_ref == "post_shift_training"


# ============================================================
# Pipeline 통합 테스트
# ============================================================

class TestSideConstraintPipeline:

    def test_all_handlers_run(self):
        from engine.constraints.base import SideConstraintPipeline
        import engine.constraints.builtin  # noqa

        cols = _make_columns([
            {"training_eligible": True, "active_minutes": 300},
            {"training_eligible": True, "active_minutes": 350},
            {"training_eligible": False, "active_minutes": 280},
        ])

        config_list = [
            {"type": "cardinality", "column_attribute": "training_eligible",
             "operator": ">=", "value": 1},
            {"type": "aggregate_avg", "column_field": "active_minutes",
             "operator": "<=", "value": 320},
        ]

        pipeline = SideConstraintPipeline(config_list)
        results = pipeline.build_all(cols, {})

        assert len(results) == 2
        assert results[0].name == "cardinality_training_eligible"
        assert results[1].name == "aggregate_avg_active_minutes"

    def test_empty_config(self):
        from engine.constraints.base import SideConstraintPipeline
        pipeline = SideConstraintPipeline([])
        results = pipeline.build_all([], {})
        assert results == []

    def test_unknown_type_skipped(self):
        from engine.constraints.base import SideConstraintPipeline
        import engine.constraints.builtin  # noqa

        config_list = [
            {"type": "nonexistent_handler"},
            {"type": "cardinality", "column_attribute": "training_eligible",
             "operator": ">=", "value": 1},
        ]
        pipeline = SideConstraintPipeline(config_list)
        assert pipeline.constraint_count == 1
        assert len(pipeline.load_errors) == 1


# ============================================================
# YAML 로딩 테스트
# ============================================================

class TestYAMLSideConstraints:

    def test_load_empty_by_default(self):
        """기본 engine_defaults에 side_constraints: [] → 빈 목록"""
        from engine.config_loader import load_side_constraints
        result = load_side_constraints(domain=None)
        assert isinstance(result, list)

    def test_load_railway_domain(self):
        """railway 도메인에서 side_constraints 로딩 (현재 미정의 → 빈 목록)"""
        from engine.config_loader import load_side_constraints
        result = load_side_constraints(domain="railway")
        assert isinstance(result, list)
