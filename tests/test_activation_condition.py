"""activation_condition 범용 엔진 테스트

constraints.yaml의 activation_condition을 _check_applicability에서
평가하는 로직을 검증합니다.
"""
import pytest
import yaml
from pathlib import Path
from core.platform.ambiguity_detector import _safe_eval, _DotDict


_BASE = Path(__file__).resolve().parents[1]


class TestActivationConditionEval:
    """_safe_eval + _DotDict로 activation_condition 평가"""

    def test_overnight_crew_true(self):
        """is_overnight_crew=True → 숙박조 제약 활성화"""
        params = _DotDict({
            "is_overnight_crew": {"value": True, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') == True"
        assert _safe_eval(cond, ctx) is True

    def test_overnight_crew_false(self):
        """is_overnight_crew=False → 숙박조 제약 비활성"""
        params = _DotDict({
            "is_overnight_crew": {"value": False, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') == True"
        assert _safe_eval(cond, ctx) is False

    def test_overnight_crew_not_set(self):
        """is_overnight_crew 미설정 → 숙박조 제약 비활성"""
        params = _DotDict({})
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') == True"
        assert _safe_eval(cond, ctx) is False

    def test_night_duty_start_not_overnight(self):
        """숙박조가 아닌 경우 → night_duty_start 활성화"""
        params = _DotDict({
            "is_overnight_crew": {"value": False, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') is not True"
        assert _safe_eval(cond, ctx) is True

    def test_night_duty_start_when_overnight(self):
        """숙박조인 경우 → night_duty_start 비활성"""
        params = _DotDict({
            "is_overnight_crew": {"value": True, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') is not True"
        assert _safe_eval(cond, ctx) is False

    def test_crew_count_provided(self):
        """승무원 수 제공됨 → 고정 제약 활성"""
        params = _DotDict({
            "day_crew_count": {"value": 32, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('day_crew_count', {}).get('value') is not None"
        assert _safe_eval(cond, ctx) is True

    def test_crew_count_not_provided(self):
        """승무원 수 미제공 → 고정 제약 비활성"""
        params = _DotDict({})
        ctx = _DotDict({"params": params})
        cond = "params.get('day_crew_count', {}).get('value') is not None"
        assert _safe_eval(cond, ctx) is False

    def test_compound_condition(self):
        """복합 조건: 숙박조 + 수면시간 비근무"""
        params = _DotDict({
            "is_overnight_crew": {"value": True, "source": "user_clarification"},
            "sleep_counts_as_work": {"value": False, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') == True and params.get('sleep_counts_as_work', {}).get('value') == False"
        assert _safe_eval(cond, ctx) is True

    def test_compound_condition_partial(self):
        """복합 조건 일부만 충족 → 비활성"""
        params = _DotDict({
            "is_overnight_crew": {"value": True, "source": "user_clarification"},
            "sleep_counts_as_work": {"value": True, "source": "user_clarification"},
        })
        ctx = _DotDict({"params": params})
        cond = "params.get('is_overnight_crew', {}).get('value') == True and params.get('sleep_counts_as_work', {}).get('value') == False"
        assert _safe_eval(cond, ctx) is False


class TestRailwayConstraintsActivation:
    """railway constraints.yaml에 정의된 activation_condition 유효성 검증"""

    @pytest.fixture
    def constraints(self):
        path = _BASE / "knowledge" / "domains" / "railway" / "constraints.yaml"
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_activation_conditions_parseable(self, constraints):
        """모든 activation_condition이 구문 오류 없이 파싱됨"""
        all_constraints = constraints.get("constraints", {})
        for cname, cdata in all_constraints.items():
            cond = cdata.get("activation_condition")
            if cond:
                # 빈 params로 평가 — 오류 없이 실행되어야 함
                ctx = _DotDict({"params": _DotDict({})})
                result = _safe_eval(cond, ctx)
                assert result is not None or result is None  # 평가 자체가 성공

    def test_overnight_constraints_identified(self, constraints):
        """숙박조 관련 activation_condition이 올바르게 태깅됨"""
        all_constraints = constraints.get("constraints", {})
        overnight_activated = []
        overnight_deactivated = []
        for cname, cdata in all_constraints.items():
            cond = cdata.get("activation_condition", "")
            if "is_overnight_crew" in cond:
                if "== True" in cond:
                    overnight_activated.append(cname)
                elif "is not True" in cond:
                    overnight_deactivated.append(cname)

        # 숙박조일 때 활성화되는 제약
        assert "night_sleep_guarantee" in overnight_activated
        assert "night_rest" in overnight_activated
        # 숙박조가 아닐 때만 활성화되는 제약
        assert "night_duty_start" in overnight_deactivated
        assert "night_duty_start_preferred" in overnight_deactivated

    def test_crew_count_constraints_conditional(self, constraints):
        """승무원 수 고정 제약이 조건부로 설정됨"""
        all_constraints = constraints.get("constraints", {})
        assert "activation_condition" in all_constraints["fixed_day_crew_count"]
        assert "activation_condition" in all_constraints["fixed_night_crew_count"]
        assert "activation_condition" in all_constraints["fixed_total_duties"]

    def test_no_activation_on_core_constraints(self, constraints):
        """핵심 제약(trip_coverage 등)은 activation_condition이 없음"""
        all_constraints = constraints.get("constraints", {})
        core = ["trip_coverage", "crew_activation_linking", "no_overlap"]
        for cname in core:
            assert "activation_condition" not in all_constraints.get(cname, {}), \
                f"{cname} should NOT have activation_condition"
