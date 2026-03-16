"""SkillIntentClassifier 단위 테스트

Fast-Path 매칭 + LLM 응답 파싱 로직을 검증합니다.
LLM 호출 자체는 모킹하여 테스트합니다.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from core.platform.intent_classifier import (
    SkillIntentClassifier,
    IntentResult,
    _load_skill_intents,
)


@pytest.fixture
def classifier():
    return SkillIntentClassifier()


class TestSkillIntentsConfig:
    """skill_intents.yaml 로드 검증"""

    def test_config_loads(self):
        config = _load_skill_intents()
        assert "problem_definition" in config
        assert "math_model" in config
        assert "analyze" in config
        assert "solver" in config

    def test_problem_definition_has_intents(self):
        config = _load_skill_intents()
        pd = config["problem_definition"]
        intents = pd["intents"]
        assert "confirm" in intents
        assert "change_objective" in intents
        assert "change_category" in intents
        assert "set_parameter" in intents
        assert "question" in intents

    def test_button_actions_defined(self):
        config = _load_skill_intents()
        pd = config["problem_definition"]
        buttons = pd["button_actions"]
        assert buttons["확인"] == "confirm"
        assert buttons["수정"] == "modify_general"
        assert buttons["취소"] == "cancel"


class TestFastPath:
    """Fast-Path (버튼 클릭) 매칭"""

    def test_exact_match_korean(self, classifier):
        result = classifier.fast_path("problem_definition", "확인")
        assert result is not None
        assert result.intent == "confirm"
        assert result.confidence == 1.0
        assert result.source == "fast_path"

    def test_exact_match_cancel(self, classifier):
        result = classifier.fast_path("problem_definition", "취소")
        assert result is not None
        assert result.intent == "cancel"

    def test_exact_match_modify(self, classifier):
        result = classifier.fast_path("problem_definition", "수정")
        assert result is not None
        assert result.intent == "modify_general"

    def test_exact_match_case_insensitive(self, classifier):
        result = classifier.fast_path("problem_definition", "OK")
        assert result is not None
        assert result.intent == "confirm"

    def test_no_match_free_text(self, classifier):
        result = classifier.fast_path("problem_definition", "목적함수를 바꾸고 싶어")
        assert result is None

    def test_no_match_partial(self, classifier):
        # "확인합니다" != "확인" → 매칭 안됨
        result = classifier.fast_path("problem_definition", "확인합니다")
        assert result is None

    def test_unknown_skill(self, classifier):
        result = classifier.fast_path("nonexistent_skill", "확인")
        assert result is None

    def test_math_model_confirm(self, classifier):
        result = classifier.fast_path("math_model", "확정")
        assert result is not None
        assert result.intent == "confirm"

    def test_math_model_regenerate(self, classifier):
        result = classifier.fast_path("math_model", "재생성")
        assert result is not None
        assert result.intent == "regenerate"

    def test_whitespace_stripped(self, classifier):
        result = classifier.fast_path("problem_definition", "  확인  ")
        assert result is not None
        assert result.intent == "confirm"


class TestParseResponse:
    """LLM 응답 파싱"""

    def test_valid_json(self, classifier):
        raw = '{"intent": "confirm", "params": {}, "confidence": 0.95}'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "confirm"
        assert result.confidence == 0.95
        assert result.source == "llm"

    def test_json_with_params(self, classifier):
        raw = '{"intent": "change_objective", "params": {"target_objective": "minimize_crew"}, "confidence": 0.88}'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "change_objective"
        assert result.params["target_objective"] == "minimize_crew"

    def test_json_with_surrounding_text(self, classifier):
        raw = 'Here is my analysis:\n{"intent": "confirm", "params": {}, "confidence": 0.9}\nDone.'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "confirm"

    def test_invalid_json_fallback(self, classifier):
        raw = "I think the user wants to confirm"
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "question"
        assert result.confidence == 0.3
        assert result.source == "fallback"

    def test_unknown_intent_fallback(self, classifier):
        raw = '{"intent": "unknown_action", "params": {}, "confidence": 0.9}'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "question"
        assert result.confidence < 0.5

    def test_missing_confidence(self, classifier):
        raw = '{"intent": "confirm", "params": {}}'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "confirm"
        assert result.confidence == 0.5  # default

    def test_solver_priority(self, classifier):
        raw = '{"intent": "priority_speed", "params": {}, "confidence": 0.85}'
        result = classifier._parse_response(raw, "solver")
        assert result.intent == "priority_speed"

    def test_analyze_reanalyze(self, classifier):
        raw = '{"intent": "reanalyze", "params": {}, "confidence": 0.92}'
        result = classifier._parse_response(raw, "analyze")
        assert result.intent == "reanalyze"


class TestBuildPrompt:
    """프롬프트 생성"""

    def test_prompt_contains_intents(self, classifier):
        config = classifier.get_skill_config("problem_definition")
        prompt = classifier._build_prompt(
            "problem_definition", config, "테스트 메시지", "상태 요약", ""
        )
        assert "confirm" in prompt
        assert "change_objective" in prompt
        assert "change_category" in prompt
        assert "테스트 메시지" in prompt
        assert "상태 요약" in prompt

    def test_prompt_contains_examples(self, classifier):
        config = classifier.get_skill_config("problem_definition")
        prompt = classifier._build_prompt(
            "problem_definition", config, "test", "", ""
        )
        assert "목적함수를 승무원 최소화로 변경" in prompt

    def test_prompt_pending_action(self, classifier):
        config = classifier.get_skill_config("problem_definition")
        prompt = classifier._build_prompt(
            "problem_definition", config, "test", "",
            "목적함수 변경 확인 대기 중"
        )
        assert "목적함수 변경 확인 대기 중" in prompt


class TestClassifyWithMockedLLM:
    """LLM 호출 모킹한 classify() 테스트"""

    def test_classify_confirm(self, classifier):
        import asyncio
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"intent": "confirm", "params": {}, "confidence": 0.95}'
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition", "좋습니다 진행해주세요"
        ))
        assert result.intent == "confirm"
        assert result.confidence == 0.95
        assert result.source == "llm"

    def test_classify_objective_change(self, classifier):
        import asyncio
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"intent": "change_objective", "params": {"target_objective": "minimize_crew"}, "confidence": 0.9}'
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition",
            "목적함수를 승무원 수 최소화로 바꾸고 싶습니다"
        ))
        assert result.intent == "change_objective"
        assert result.params.get("target_objective") == "minimize_crew"

    def test_classify_error_fallback(self, classifier):
        import asyncio
        mock_model = MagicMock()
        mock_model.generate_content = MagicMock(side_effect=Exception("API error"))

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition", "test"
        ))
        assert result.intent == "question"
        assert result.source == "fallback"

    def test_classify_unknown_skill(self, classifier):
        import asyncio
        mock_model = MagicMock()
        result = asyncio.run(classifier.classify(
            mock_model, "nonexistent", "test"
        ))
        assert result.intent == "question"
        assert result.source == "fallback"
