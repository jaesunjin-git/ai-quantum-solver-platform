"""AmbiguityDetector 범용 엔진 테스트"""
import pytest
from core.platform.ambiguity_detector import (
    AmbiguityDetector,
    ClarificationQuestion,
    format_minutes,
    _safe_eval,
)


class TestFormatMinutes:
    def test_basic(self):
        assert format_minutes(360) == "06:00"
        assert format_minutes(1020) == "17:00"
        assert format_minutes(270) == "04:30"

    def test_none(self):
        assert format_minutes(None) == "??"

    def test_float(self):
        assert format_minutes(270.5) == "04:30"


class TestSafeEval:
    def test_basic_comparison(self):
        assert _safe_eval("x > 5", {"x": 10}) is True
        assert _safe_eval("x > 5", {"x": 3}) is False

    def test_none_check(self):
        assert _safe_eval("x is not None and x < 360", {"x": 270}) is True
        assert _safe_eval("x is not None and x < 360", {"x": None}) is False

    def test_dict_get(self):
        ctx = {"params": {"day_crew_count": {"source": None}}}
        assert _safe_eval("params.get('day_crew_count', {}).get('source') is None", ctx) is True

    def test_nested_access(self):
        ctx = {"phase1": {"min_time": 270, "trip_count": 45}}
        assert _safe_eval("phase1.get('min_time', 0) < 360", ctx) is True

    def test_invalid_expr(self):
        assert _safe_eval("import os", {}) is None


class TestAmbiguityDetectorInit:
    def test_load_railway_rules(self):
        detector = AmbiguityDetector("railway")
        assert len(detector.rules) > 0
        assert "overnight_crew_pattern" in detector.rules

    def test_load_nonexistent_domain(self):
        detector = AmbiguityDetector("nonexistent_domain_xyz")
        assert len(detector.rules) == 0


class TestDetectOvernightCrewPattern:
    """새벽 DIA 감지 → 숙박조 질문 트리거"""

    def setup_method(self):
        self.detector = AmbiguityDetector("railway")

    def test_dawn_trip_triggers_question(self):
        """06:00 이전 DIA → 숙박조 질문 발생"""
        params = {}
        phase1 = {"min_time": 270.0, "trip_count": 45}  # 04:30 출발
        questions = self.detector.detect(params, phase1)
        # overnight_crew_pattern 규칙이 트리거되어야 함
        overnight_qs = [q for q in questions if q.rule_id == "overnight_crew_pattern"]
        assert len(overnight_qs) > 0
        assert "숙박" in overnight_qs[0].text or "새벽" in overnight_qs[0].text

    def test_dawn_trip_text_vars_resolved(self):
        """text_vars의 format_minutes 표현식이 실제 시간으로 해석됨"""
        params = {}
        phase1 = {"min_time": 270.0, "trip_count": 45}  # 04:30
        questions = self.detector.detect(params, phase1)
        overnight_qs = [q for q in questions if q.rule_id == "overnight_crew_pattern"]
        assert len(overnight_qs) > 0
        # {min_time_hhmm}이 "04:30"으로 치환되어야 함
        assert "04:30" in overnight_qs[0].text
        assert "{min_time_hhmm}" not in overnight_qs[0].text

    def test_no_dawn_trip_no_question(self):
        """06:00 이후 DIA만 → 숙박조 질문 없음"""
        params = {}
        phase1 = {"min_time": 420.0, "trip_count": 45}  # 07:00 출발
        questions = self.detector.detect(params, phase1)
        overnight_qs = [q for q in questions if q.rule_id == "overnight_crew_pattern"]
        assert len(overnight_qs) == 0

    def test_no_trip_data_no_question(self):
        """trip 데이터 없음 → 질문 없음"""
        params = {}
        phase1 = {}
        questions = self.detector.detect(params, phase1)
        overnight_qs = [q for q in questions if q.rule_id == "overnight_crew_pattern"]
        assert len(overnight_qs) == 0

    def test_already_answered_skipped(self):
        """이미 답변한 질문은 재질문하지 않음"""
        params = {}
        phase1 = {"min_time": 270.0, "trip_count": 45}
        answered = {"overnight_crew_pattern.is_overnight"}
        questions = self.detector.detect(params, phase1, answered_ids=answered)
        overnight_qs = [q for q in questions if q.question_id == "overnight_crew_pattern.is_overnight"]
        assert len(overnight_qs) == 0


class TestDetectCrewCountMissing:
    """승무원 수 누락 감지"""

    def setup_method(self):
        self.detector = AmbiguityDetector("railway")

    def test_missing_crew_count_triggers_for_balance(self):
        """balance_workload 목적함수에서만 crew count 질문 발생"""
        params = {
            "day_crew_count": {"source": None},
            "night_crew_count": {"source": None},
        }
        phase1 = {"min_time": 420.0}
        questions = self.detector.detect(params, phase1, objective_id="balance_workload")
        crew_qs = [q for q in questions if q.rule_id == "crew_count_missing"]
        assert len(crew_qs) > 0

    def test_missing_crew_count_skipped_for_minimize(self):
        """minimize_duties 목적함수에서는 crew count 질문 안 함"""
        params = {
            "day_crew_count": {"source": None},
            "night_crew_count": {"source": None},
        }
        phase1 = {"min_time": 420.0}
        questions = self.detector.detect(params, phase1, objective_id="minimize_duties")
        crew_qs = [q for q in questions if q.rule_id == "crew_count_missing"]
        assert len(crew_qs) == 0

    def test_crew_count_present_no_trigger(self):
        params = {
            "day_crew_count": {"value": 32, "source": "parameters.csv"},
            "night_crew_count": {"value": 13, "source": "parameters.csv"},
        }
        phase1 = {"min_time": 420.0}
        questions = self.detector.detect(params, phase1)
        crew_qs = [q for q in questions if q.rule_id == "crew_count_missing"]
        assert len(crew_qs) == 0


class TestApplyAnswer:
    """답변 적용 테스트"""

    def setup_method(self):
        self.detector = AmbiguityDetector("railway")

    def test_yes_no_answer_yes(self):
        q = ClarificationQuestion(
            rule_id="overnight_crew_pattern",
            question_def={
                "id": "is_overnight",
                "type": "yes_no",
                "on_yes": {
                    "set_params": {
                        "is_overnight_crew": {"value": True, "source": "user_clarification"},
                    },
                    "follow_up": ["overnight_sleep", "sleep_as_work"],
                },
                "on_no": {
                    "set_params": {
                        "is_overnight_crew": {"value": False, "source": "user_clarification"},
                    },
                },
            },
            resolved_text="test",
        )
        params = {}
        result = self.detector.apply_answer(q, True, params)
        assert params["is_overnight_crew"]["value"] is True
        assert "overnight_sleep" in result["follow_up"]

    def test_yes_no_answer_no(self):
        q = ClarificationQuestion(
            rule_id="overnight_crew_pattern",
            question_def={
                "id": "is_overnight",
                "type": "yes_no",
                "on_yes": {"set_params": {"is_overnight_crew": {"value": True, "source": "user_clarification"}}},
                "on_no": {"set_params": {"is_overnight_crew": {"value": False, "source": "user_clarification"}}},
            },
            resolved_text="test",
        )
        params = {}
        result = self.detector.apply_answer(q, False, params)
        assert params["is_overnight_crew"]["value"] is False
        assert result["follow_up"] == []

    def test_numeric_answer(self):
        q = ClarificationQuestion(
            rule_id="overnight_crew_pattern",
            question_def={
                "id": "overnight_sleep",
                "type": "numeric",
                "param": "min_overnight_sleep_minutes",
                "transform": "value * 60",
                "unit": "시간",
                "default": 6,
            },
            resolved_text="test",
        )
        params = {}
        result = self.detector.apply_answer(q, 6, params)
        assert params["min_overnight_sleep_minutes"]["value"] == 360  # 6 * 60
        assert params["min_overnight_sleep_minutes"]["source"] == "user_clarification"

    def test_multi_input_answer(self):
        q = ClarificationQuestion(
            rule_id="crew_count_missing",
            question_def={
                "id": "crew_counts",
                "type": "multi_input",
                "fields": [
                    {"id": "day_crew_count", "label": "주간", "type": "numeric"},
                    {"id": "night_crew_count", "label": "야간", "type": "numeric"},
                ],
            },
            resolved_text="test",
        )
        params = {}
        result = self.detector.apply_answer(q, {"day_crew_count": 32, "night_crew_count": 13}, params)
        assert params["day_crew_count"]["value"] == 32
        assert params["night_crew_count"]["value"] == 13


class TestImportanceSorting:
    """critical 질문이 high보다 먼저 나오는지 확인"""

    def test_critical_first(self):
        detector = AmbiguityDetector("railway")
        # 두 규칙 모두 트리거되는 조건
        params = {
            "day_crew_count": {"source": None},
            "night_crew_count": {"source": None},
        }
        phase1 = {"min_time": 270.0, "trip_count": 45}
        questions = detector.detect(params, phase1)
        if len(questions) >= 2:
            # critical 규칙의 질문이 먼저
            importances = []
            for q in questions:
                rule = detector.rules.get(q.rule_id, {})
                importances.append(rule.get("importance", "low"))
            # critical은 high보다 앞에 있어야 함
            seen_high = False
            for imp in importances:
                if imp == "high":
                    seen_high = True
                if imp == "critical" and seen_high:
                    pytest.fail("critical question came after high question")


class TestQuestionSerialization:
    def test_to_dict(self):
        q = ClarificationQuestion(
            rule_id="test_rule",
            question_def={"id": "q1", "type": "yes_no"},
            resolved_text="테스트 질문입니다",
        )
        d = q.to_dict()
        assert d["rule_id"] == "test_rule"
        assert d["question_id"] == "q1"
        assert d["type"] == "yes_no"
        assert d["text"] == "테스트 질문입니다"
