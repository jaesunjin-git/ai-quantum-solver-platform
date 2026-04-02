"""
tests/test_depot_validators.py
거점 검증기 — Stage 5 (DepotPolicyValidator) + Stage 6 (DepotSolutionValidator).
"""

from __future__ import annotations

import pytest

from engine.column_generator import TaskItem, FeasibleColumn
from engine.validation.base import Severity
from engine.validation.generic.depot import (
    DepotPolicyValidator,
    DepotSolutionValidator,
)


# ════════════════════════════════════════════════════════════════
# Stage 5: DepotPolicyValidator
# ════════════════════════════════════════════════════════════════

class TestDepotPolicyValidator:
    """pre-solve depot 정책-데이터 정합성 검증"""

    def _make_tasks(self, allowed_depots_list):
        """allowed_depots 목록으로 task 생성"""
        return [
            TaskItem(id=i, dep_time=400+i*50, arr_time=440+i*50, duration=40,
                    start_location="A", end_location="B",
                    allowed_depots=frozenset(d) if d else frozenset())
            for i, d in enumerate(allowed_depots_list)
        ]

    def test_multi_policy_skips(self):
        """multi 정책이면 검증 건너뜀"""
        v = DepotPolicyValidator()
        result = v.validate({"depot_policy": {"type": "multi"}, "tasks": []})
        assert result.passed
        assert len(result.items) == 0

    def test_single_policy_all_wildcard_error(self):
        """single 정책 + 전부 wildcard → ERROR"""
        tasks = self._make_tasks([set(), set(), set()])
        v = DepotPolicyValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "tasks": tasks,
        })
        assert not result.passed
        errors = [i for i in result.items if i.severity == Severity.ERROR]
        assert any("DEPOT_POLICY_NO_DATA" == e.code for e in errors)

    def test_single_policy_high_wildcard_warning(self):
        """single 정책 + 50% wildcard → WARNING"""
        tasks = self._make_tasks([{"노포"}, {"노포"}, set(), set()])
        v = DepotPolicyValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "tasks": tasks,
        })
        assert result.passed  # warning은 통과
        warnings = [i for i in result.items if i.severity == Severity.WARNING]
        assert any("DEPOT_HIGH_WILDCARD_RATIO" == w.code for w in warnings)

    def test_single_policy_all_assigned_ok(self):
        """single 정책 + 전부 할당 → INFO만"""
        tasks = self._make_tasks([{"노포"}, {"노포"}, {"신평"}])
        v = DepotPolicyValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "tasks": tasks,
        })
        assert result.passed
        infos = [i for i in result.items if i.severity == Severity.INFO]
        assert any("DEPOT_DISTRIBUTION" == i.code for i in infos)

    def test_depot_source_trace(self):
        """depot_source 추적 (csv/params/wildcard)"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="A", end_location="B",
                    raw_depot="노포", allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="A", end_location="B",
                    allowed_depots=frozenset({"신평"})),
            TaskItem(id=3, dep_time=500, arr_time=540, duration=40,
                    start_location="A", end_location="B"),
        ]
        v = DepotPolicyValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "tasks": tasks,
        })
        trace = next((i for i in result.items if i.code == "DEPOT_SOURCE_TRACE"), None)
        assert trace is not None
        assert trace.context["source_counts"]["csv"] == 1
        assert trace.context["source_counts"]["params"] == 1
        assert trace.context["source_counts"]["wildcard"] == 1

    def test_no_tasks_no_error(self):
        """task 없으면 검증 건너뜀"""
        v = DepotPolicyValidator()
        result = v.validate({"depot_policy": {"type": "single"}, "tasks": []})
        assert result.passed
        assert len(result.items) == 0


# ════════════════════════════════════════════════════════════════
# Stage 6: DepotSolutionValidator
# ════════════════════════════════════════════════════════════════

class TestDepotSolutionValidator:
    """post-solve 거점 분리 검증"""

    def test_multi_policy_skips(self):
        """multi 정책이면 검증 건너뜀"""
        v = DepotSolutionValidator()
        result = v.validate({"depot_policy": {"type": "multi"}, "columns": [], "tasks": []})
        assert result.passed
        assert len(result.items) == 0

    def test_clean_solution_ok(self):
        """거점 분리 정상 솔루션"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="A", end_location="B",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="B", end_location="A",
                    allowed_depots=frozenset({"노포"})),
        ]
        columns = [
            FeasibleColumn(id=1, trips=[1, 2], start_depot="노포", end_depot="노포"),
        ]
        v = DepotSolutionValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "columns": columns,
            "tasks": tasks,
        })
        assert result.passed
        infos = [i for i in result.items if i.code == "DEPOT_SOLUTION_OK"]
        assert len(infos) == 1

    def test_cross_depot_error(self):
        """cross-depot column 감지 → ERROR"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="A", end_location="B",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="B", end_location="A",
                    allowed_depots=frozenset({"신평"})),
        ]
        columns = [
            FeasibleColumn(id=1, trips=[1, 2], start_depot="노포", end_depot="노포"),
        ]
        v = DepotSolutionValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "columns": columns,
            "tasks": tasks,
        })
        assert not result.passed
        errors = [i for i in result.items if i.code == "DEPOT_CROSS_DEPOT_SOLUTION"]
        assert len(errors) == 1
        assert errors[0].context["incompatible_count"] == 1

    def test_wildcard_in_column_ok(self):
        """wildcard task 포함 column은 cross-depot 아님"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="A", end_location="B",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="B", end_location="A",
                    allowed_depots=frozenset()),  # wildcard
        ]
        columns = [
            FeasibleColumn(id=1, trips=[1, 2], start_depot="노포", end_depot="노포"),
        ]
        v = DepotSolutionValidator()
        result = v.validate({
            "depot_policy": {"type": "single"},
            "columns": columns,
            "tasks": tasks,
        })
        assert result.passed


# ════════════════════════════════════════════════════════════════
# Registry 등록 확인
# ════════════════════════════════════════════════════════════════

class TestDepotRegistration:
    """ValidationRegistry에 depot validators가 등록되는지 확인"""

    def test_register_all_includes_depot(self):
        from engine.validation.registry import ValidationRegistry
        from engine.validation.generic import register_all

        registry = ValidationRegistry()
        register_all(registry)

        validators = registry.list_validators()
        names = {v["name"] for v in validators}
        assert "DepotPolicyValidator" in names
        assert "DepotSolutionValidator" in names

    def test_depot_policy_stage_5(self):
        from engine.validation.registry import ValidationRegistry
        from engine.validation.generic import register_all

        registry = ValidationRegistry()
        register_all(registry)

        stage5 = registry.list_validators(stage=5)
        names = {v["name"] for v in stage5}
        assert "DepotPolicyValidator" in names

    def test_depot_solution_stage_6(self):
        from engine.validation.registry import ValidationRegistry
        from engine.validation.generic import register_all

        registry = ValidationRegistry()
        register_all(registry)

        stage6 = registry.list_validators(stage=6)
        names = {v["name"] for v in stage6}
        assert "DepotSolutionValidator" in names
