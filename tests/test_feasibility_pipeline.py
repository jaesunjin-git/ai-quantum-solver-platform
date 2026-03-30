"""
Feasibility Pipeline 테스트
============================
Phase 1a~1d 검증:
  - FeasibilityCheck 인터페이스 + Registry + Pipeline
  - built-in handler: max_value, min_value, break_window, min_turnaround
  - YAML 로딩 + column_generator 연동
  - _param 접미사 규칙 (resolve_param)
"""

import pytest
from dataclasses import dataclass
from typing import List


# ── 테스트용 mock column ────────────────────────────────────

@dataclass
class MockColumn:
    """FeasibleColumn 대용 (테스트용)"""
    id: int = 1
    trips: List[int] = None
    idle_minutes: int = 0
    active_minutes: int = 0
    elapsed_minutes: int = 0
    span_minutes: int = 0
    start_time: int = 0
    end_time: int = 0

    def __post_init__(self):
        if self.trips is None:
            self.trips = []


@dataclass
class MockTask:
    """TaskItem 대용 (테스트용)"""
    id: int = 1
    dep_time: int = 0
    arr_time: int = 0
    start_location: str = "A"
    end_location: str = "B"


# ============================================================
# resolve_param 테스트
# ============================================================

class TestResolveParam:
    """_param 접미사 규칙 검증"""

    def test_param_reference_takes_priority(self):
        """_param으로 참조하면 params에서 값 조회"""
        from engine.feasibility.base import resolve_param
        config = {"limit_param": "max_idle_time", "limit": 999}
        params = {"max_idle_time": 300}
        assert resolve_param(config, "limit", params) == 300

    def test_direct_value_when_no_param(self):
        """_param 없으면 직접 값 사용"""
        from engine.feasibility.base import resolve_param
        config = {"limit": 360}
        params = {}
        assert resolve_param(config, "limit", params) == 360

    def test_param_fallback_to_direct(self):
        """_param이 있지만 params에 없으면 직접 값으로 fallback"""
        from engine.feasibility.base import resolve_param
        config = {"limit_param": "nonexistent_key", "limit": 500}
        params = {}
        assert resolve_param(config, "limit", params) == 500

    def test_default_when_both_missing(self):
        """둘 다 없으면 default 반환"""
        from engine.feasibility.base import resolve_param
        config = {}
        params = {}
        assert resolve_param(config, "limit", params, default=42) == 42

    def test_none_param_value_falls_through(self):
        """params에 키가 있지만 값이 None이면 직접 값으로 fallback"""
        from engine.feasibility.base import resolve_param
        config = {"limit_param": "max_idle", "limit": 300}
        params = {"max_idle": None}
        assert resolve_param(config, "limit", params) == 300


# ============================================================
# Registry 테스트
# ============================================================

class TestFeasibilityCheckRegistry:
    """Registry 등록/조회 검증"""

    def test_builtin_handlers_registered(self):
        """built-in handler 4종이 자동 등록되어 있는지"""
        from engine.feasibility.builtin import register_builtin_handlers  # noqa
        from engine.feasibility.base import FeasibilityCheckRegistry

        for name in ["max_value", "min_value", "break_window", "min_turnaround"]:
            assert FeasibilityCheckRegistry.get(name) is not None, f"{name} not registered"

    def test_unknown_type_returns_none(self):
        from engine.feasibility.base import FeasibilityCheckRegistry
        assert FeasibilityCheckRegistry.get("nonexistent_type") is None

    def test_registered_types_list(self):
        from engine.feasibility.builtin import register_builtin_handlers  # noqa
        from engine.feasibility.base import FeasibilityCheckRegistry
        types = FeasibilityCheckRegistry.registered_types()
        assert "max_value" in types
        assert "break_window" in types


# ============================================================
# MaxValueCheck 테스트
# ============================================================

class TestMaxValueCheck:
    """max_value handler 검증"""

    def test_pass_when_under_limit(self):
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn(idle_minutes=200)
        result = check.check(col, {"field": "idle_minutes", "limit": 300}, {})
        assert result.feasible is True

    def test_reject_when_over_limit(self):
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn(idle_minutes=350)
        result = check.check(col, {"field": "idle_minutes", "limit": 300}, {})
        assert result.feasible is False
        assert "350" in result.reason
        assert "300" in result.reason

    def test_pass_at_exact_limit(self):
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn(idle_minutes=300)
        result = check.check(col, {"field": "idle_minutes", "limit": 300}, {})
        assert result.feasible is True

    def test_param_reference(self):
        """limit_param으로 params에서 값 조회"""
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn(active_minutes=400)
        config = {"field": "active_minutes", "limit_param": "max_active_time"}
        params = {"max_active_time": 360}
        result = check.check(col, config, params)
        assert result.feasible is False

    def test_skip_when_field_missing(self):
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn()
        result = check.check(col, {"field": "nonexistent_field", "limit": 100}, {})
        assert result.feasible is True  # skip, not reject

    def test_skip_when_no_limit(self):
        from engine.feasibility.builtin import MaxValueCheck
        check = MaxValueCheck()
        col = MockColumn(idle_minutes=999)
        result = check.check(col, {"field": "idle_minutes"}, {})
        assert result.feasible is True  # no limit = skip


# ============================================================
# MinValueCheck 테스트
# ============================================================

class TestMinValueCheck:

    def test_pass_when_above_limit(self):
        from engine.feasibility.builtin import MinValueCheck
        check = MinValueCheck()
        col = MockColumn(active_minutes=200)
        result = check.check(col, {"field": "active_minutes", "limit": 100}, {})
        assert result.feasible is True

    def test_reject_when_below_limit(self):
        from engine.feasibility.builtin import MinValueCheck
        check = MinValueCheck()
        col = MockColumn(active_minutes=50)
        result = check.check(col, {"field": "active_minutes", "limit": 100}, {})
        assert result.feasible is False


# ============================================================
# BreakWindowCheck 테스트
# ============================================================

class TestBreakWindowCheck:
    """break_window handler 검증 — 시간 구간 내 최소 연속 공백"""

    def _make_task_map(self, trip_times):
        """[(dep, arr), ...] → {id: MockTask}"""
        return {
            i + 1: MockTask(id=i + 1, dep_time=dep, arr_time=arr)
            for i, (dep, arr) in enumerate(trip_times)
        }

    def test_pass_with_sufficient_gap(self):
        """11:00~14:00 사이에 40분 공백 → 30분 요구 충족"""
        from engine.feasibility.builtin import BreakWindowCheck
        check = BreakWindowCheck()

        # trip1: 10:00~11:30 (600~690), trip2: 12:10~13:00 (730~780)
        # gap: 690~730 = 40분 (window 660~840 내)
        task_map = self._make_task_map([(600, 690), (730, 780)])
        col = MockColumn(trips=[1, 2], start_time=540, end_time=820)

        config = {
            "windows": [{"start": 660, "end": 840, "min_gap": 30}],
        }
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is True

    def test_reject_with_insufficient_gap(self):
        """11:00~14:00 사이에 최대 15분 공백 → 30분 요구 미충족"""
        from engine.feasibility.builtin import BreakWindowCheck
        check = BreakWindowCheck()

        # window 660~840 (180분)을 거의 빈틈없이 채움
        # trip1: 660~720, trip2: 735~790, trip3: 800~840
        # gaps: 720~735=15분, 790~800=10분 — 최대 15분
        task_map = self._make_task_map([(660, 720), (735, 790), (800, 840)])
        col = MockColumn(trips=[1, 2, 3], start_time=600, end_time=880)

        config = {
            "windows": [{"start": 660, "end": 840, "min_gap": 30}],
        }
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is False
        assert "max_gap=15" in result.reason

    def test_pass_when_no_trips_in_window(self):
        """window 시간대에 trip이 없으면 전체가 공백 → 통과"""
        from engine.feasibility.builtin import BreakWindowCheck
        check = BreakWindowCheck()

        # trip1: 08:00~09:00 (480~540) — window 660~840 밖
        task_map = self._make_task_map([(480, 540)])
        col = MockColumn(trips=[1], start_time=420, end_time=580)

        config = {
            "windows": [{"start": 660, "end": 840, "min_gap": 30}],
        }
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is True

    def test_pass_with_param_reference(self):
        """_param으로 window 파라미터 참조"""
        from engine.feasibility.builtin import BreakWindowCheck
        check = BreakWindowCheck()

        task_map = self._make_task_map([(600, 690), (730, 780)])
        col = MockColumn(trips=[1, 2], start_time=540, end_time=820)

        config = {
            "windows": [{
                "start_param": "meal_start",
                "end_param": "meal_end",
                "min_gap_param": "meal_break_min",
            }],
        }
        params = {
            "_task_map": task_map,
            "meal_start": 660,
            "meal_end": 840,
            "meal_break_min": 30,
        }
        result = check.check(col, config, params)
        assert result.feasible is True

    def test_skip_when_no_task_map(self):
        """_task_map 없으면 skip (reject 아님)"""
        from engine.feasibility.builtin import BreakWindowCheck
        check = BreakWindowCheck()
        col = MockColumn(trips=[1, 2])
        config = {"windows": [{"start": 660, "end": 840, "min_gap": 30}]}
        result = check.check(col, config, {})
        assert result.feasible is True


# ============================================================
# MinTurnaroundCheck 테스트
# ============================================================

class TestMinTurnaroundCheck:
    """min_turnaround handler 검증 — 연속 trip 간 최소 간격"""

    def _make_task_map(self, trip_times):
        return {
            i + 1: MockTask(id=i + 1, dep_time=dep, arr_time=arr)
            for i, (dep, arr) in enumerate(trip_times)
        }

    def test_pass_with_sufficient_gap(self):
        """trip 간 90분 간격 → 60분 요구 충족"""
        from engine.feasibility.builtin import MinTurnaroundCheck
        check = MinTurnaroundCheck()

        # trip1: arr=600, trip2: dep=690 → gap=90
        task_map = self._make_task_map([(500, 600), (690, 780)])
        col = MockColumn(trips=[1, 2])

        config = {"min_gap": 60}
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is True

    def test_reject_with_insufficient_gap(self):
        """trip 간 20분 간격 → 60분 요구 미충족"""
        from engine.feasibility.builtin import MinTurnaroundCheck
        check = MinTurnaroundCheck()

        # trip1: arr=600, trip2: dep=620 → gap=20
        task_map = self._make_task_map([(500, 600), (620, 700)])
        col = MockColumn(trips=[1, 2])

        config = {"min_gap": 60}
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is False
        assert "gap=20" in result.reason

    def test_pass_single_trip(self):
        """trip 1개면 turnaround 체크 불필요 → 통과"""
        from engine.feasibility.builtin import MinTurnaroundCheck
        check = MinTurnaroundCheck()

        task_map = self._make_task_map([(500, 600)])
        col = MockColumn(trips=[1])

        config = {"min_gap": 60}
        params = {"_task_map": task_map}
        result = check.check(col, config, params)
        assert result.feasible is True

    def test_param_reference(self):
        """min_gap_param으로 params에서 값 조회"""
        from engine.feasibility.builtin import MinTurnaroundCheck
        check = MinTurnaroundCheck()

        task_map = self._make_task_map([(500, 600), (620, 700)])
        col = MockColumn(trips=[1, 2])

        config = {"min_gap_param": "post_arrival_rest_minutes_min"}
        params = {"_task_map": task_map, "post_arrival_rest_minutes_min": 60}
        result = check.check(col, config, params)
        assert result.feasible is False


# ============================================================
# Pipeline 통합 테스트
# ============================================================

class TestFeasibilityPipeline:
    """Pipeline 전체 동작 검증"""

    def test_all_pass(self):
        """모든 check 통과"""
        from engine.feasibility.base import FeasibilityPipeline
        import engine.feasibility.builtin  # noqa — 자동 등록

        checks = [
            {"type": "max_value", "field": "idle_minutes", "limit": 300, "action": "reject"},
            {"type": "max_value", "field": "active_minutes", "limit": 360, "action": "reject"},
        ]
        pipeline = FeasibilityPipeline(checks)
        col = MockColumn(idle_minutes=200, active_minutes=300)
        result = pipeline.run(col, {})
        assert result.feasible is True
        assert result.checks_run == 2
        assert result.checks_passed == 2

    def test_first_reject_stops(self):
        """첫 번째 reject에서 즉시 중단"""
        from engine.feasibility.base import FeasibilityPipeline
        import engine.feasibility.builtin  # noqa

        checks = [
            {"type": "max_value", "field": "idle_minutes", "limit": 100, "action": "reject"},
            {"type": "max_value", "field": "active_minutes", "limit": 360, "action": "reject"},
        ]
        pipeline = FeasibilityPipeline(checks)
        col = MockColumn(idle_minutes=200, active_minutes=300)
        result = pipeline.run(col, {})
        assert result.feasible is False
        assert result.checks_run == 1  # 두 번째 체크 미실행
        assert "idle_minutes" in result.reject_reason

    def test_penalize_continues(self):
        """action=penalize이면 위반해도 계속 진행"""
        from engine.feasibility.base import FeasibilityPipeline
        import engine.feasibility.builtin  # noqa

        checks = [
            {"type": "max_value", "field": "idle_minutes", "limit": 100, "action": "penalize"},
            {"type": "max_value", "field": "active_minutes", "limit": 360, "action": "reject"},
        ]
        pipeline = FeasibilityPipeline(checks)
        col = MockColumn(idle_minutes=200, active_minutes=300)
        result = pipeline.run(col, {})
        assert result.feasible is True
        assert result.checks_run == 2

    def test_unknown_type_skipped(self):
        """미등록 type은 경고 후 skip"""
        from engine.feasibility.base import FeasibilityPipeline
        import engine.feasibility.builtin  # noqa

        checks = [
            {"type": "nonexistent_handler", "field": "x", "action": "reject"},
            {"type": "max_value", "field": "idle_minutes", "limit": 300, "action": "reject"},
        ]
        pipeline = FeasibilityPipeline(checks)
        assert pipeline.check_count == 1  # nonexistent 제외
        assert len(pipeline.load_errors) == 1

    def test_empty_pipeline(self):
        """check 목록이 비어있으면 항상 통과"""
        from engine.feasibility.base import FeasibilityPipeline
        pipeline = FeasibilityPipeline([])
        col = MockColumn()
        result = pipeline.run(col, {})
        assert result.feasible is True
        assert result.checks_run == 0


# ============================================================
# YAML 로딩 테스트
# ============================================================

class TestYAMLLoading:
    """config_loader.load_feasibility_checks 검증"""

    def test_defaults_load(self):
        """기본 YAML(feasibility_defaults.yaml)이 로딩됨"""
        from engine.config_loader import load_feasibility_checks
        checks = load_feasibility_checks(domain=None)
        assert len(checks) >= 3  # max_idle, max_active, max_span
        types = [c["type"] for c in checks]
        assert "max_value" in types

    def test_railway_domain_override(self):
        """railway 도메인 YAML이 defaults를 대체"""
        from engine.config_loader import load_feasibility_checks
        checks = load_feasibility_checks(domain="railway")
        assert len(checks) >= 3
        # railway YAML에 max_value checks가 있어야 함
        fields = [c.get("field") for c in checks]
        assert "idle_minutes" in fields
        assert "active_minutes" in fields
        assert "elapsed_minutes" in fields

    def test_nonexistent_domain_falls_back(self):
        """존재하지 않는 도메인이면 defaults 사용"""
        from engine.config_loader import load_feasibility_checks
        checks = load_feasibility_checks(domain="nonexistent_domain_xyz")
        assert len(checks) >= 3  # defaults


# ============================================================
# Column Generator 연동 테스트
# ============================================================

class TestColumnGeneratorIntegration:
    """column_generator의 pipeline 연동 검증"""

    def test_pipeline_initialized(self):
        """BaseColumnGenerator가 pipeline을 초기화하는지"""
        from engine.column_generator import BaseColumnGenerator, BaseColumnConfig, TaskItem

        tasks = [TaskItem(id=1, dep_time=480, arr_time=520,
                          start_location="A", end_location="B", duration=40)]
        config = BaseColumnConfig()
        gen = BaseColumnGenerator(tasks, config)

        assert hasattr(gen, '_feasibility_pipeline')
        assert gen._feasibility_pipeline.check_count >= 0

    def test_feasibility_params_include_config(self):
        """_get_feasibility_params가 config 필드를 포함하는지"""
        from engine.column_generator import BaseColumnGenerator, BaseColumnConfig, TaskItem

        tasks = [TaskItem(id=1, dep_time=480, arr_time=520,
                          start_location="A", end_location="B", duration=40)]
        config = BaseColumnConfig()
        config.max_idle_time = 300
        config.max_active_time = 360
        gen = BaseColumnGenerator(tasks, config)

        params = gen._get_feasibility_params()
        assert params["max_idle_time"] == 300
        assert params["max_active_time"] == 360
        assert "_task_map" in params
        assert 1 in params["_task_map"]
