"""Intent Log 기능 테스트

IntentLogDB 모델, log_intent 함수, intent_log_router API를 검증합니다.
"""
import pytest
from unittest.mock import patch, MagicMock
from core.platform.intent_classifier import IntentResult, log_intent


class TestIntentLogDB:
    """IntentLogDB 모델 검증"""

    def test_model_exists(self):
        from core.models import IntentLogDB
        assert IntentLogDB.__tablename__ == "intent_logs"
        assert IntentLogDB.__table_args__ == {"schema": "core"}

    def test_model_columns(self):
        from core.models import IntentLogDB
        columns = {c.name for c in IntentLogDB.__table__.columns}
        expected = {
            "id", "project_id", "skill_name", "message",
            "intent", "confidence", "source", "params_json",
            "pipeline_stage", "created_at",
        }
        assert expected.issubset(columns)


class TestLogIntent:
    """log_intent 함수 동작 검증"""

    @patch("core.database.SessionLocal")
    def test_log_intent_fast_path(self, mock_session_local):
        """fast_path 결과가 DB에 기록되는지"""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        result = IntentResult(intent="confirm", confidence=1.0, source="fast_path")
        log_intent("42", "확인", result, skill_name="problem_definition")

        mock_db.add.assert_called_once()
        added_row = mock_db.add.call_args[0][0]
        assert added_row.project_id == 42
        assert added_row.intent == "confirm"
        assert added_row.confidence == 1.0
        assert added_row.source == "fast_path"
        assert added_row.skill_name == "problem_definition"
        assert added_row.message == "확인"
        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    @patch("core.database.SessionLocal")
    def test_log_intent_llm_with_params(self, mock_session_local):
        """LLM 결과 + params가 JSON으로 기록되는지"""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        result = IntentResult(
            intent="change_objective",
            params={"target_objective": "minimize_crew"},
            confidence=0.92,
            source="llm",
        )
        log_intent("10", "목적함수 변경", result, skill_name="problem_definition",
                    pipeline_stage="problem_definition")

        added_row = mock_db.add.call_args[0][0]
        assert added_row.intent == "change_objective"
        assert added_row.confidence == 0.92
        assert added_row.source == "llm"
        assert '"target_objective"' in added_row.params_json
        assert added_row.pipeline_stage == "problem_definition"

    @patch("core.database.SessionLocal")
    def test_log_intent_no_project_id(self, mock_session_local):
        """project_id가 None이어도 오류 없이 기록"""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        result = IntentResult(intent="question", confidence=0.3, source="fallback")
        log_intent(None, "테스트", result)

        added_row = mock_db.add.call_args[0][0]
        assert added_row.project_id is None
        mock_db.commit.assert_called_once()

    @patch("core.database.SessionLocal")
    def test_log_intent_db_error_silent(self, mock_session_local):
        """DB 오류 시 예외가 전파되지 않음"""
        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("DB connection lost")
        mock_session_local.return_value = mock_db

        result = IntentResult(intent="confirm", confidence=1.0, source="fast_path")
        # 예외 발생하지 않아야 함
        log_intent("1", "확인", result)
        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()

    @patch("core.database.SessionLocal")
    def test_log_intent_message_truncated(self, mock_session_local):
        """메시지가 2000자 이상이면 잘림"""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        long_message = "가" * 3000
        result = IntentResult(intent="question", confidence=0.5, source="llm")
        log_intent("1", long_message, result)

        added_row = mock_db.add.call_args[0][0]
        assert len(added_row.message) == 2000

    @patch("core.database.SessionLocal")
    def test_log_intent_empty_params(self, mock_session_local):
        """빈 params → params_json은 None"""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        result = IntentResult(intent="confirm", confidence=1.0, source="fast_path")
        log_intent("1", "ok", result)

        added_row = mock_db.add.call_args[0][0]
        assert added_row.params_json is None


class TestIntentLogRouter:
    """intent_log_router 등록 검증"""

    def test_router_registered(self):
        from main import app
        routes = [r.path for r in app.routes]
        assert "/api/intent-logs" in routes or any("/api/intent-logs" in r for r in routes)

    def test_router_has_stats_endpoint(self):
        from main import app
        paths = [r.path for r in app.routes]
        assert "/api/intent-logs/stats" in paths

    def test_router_has_low_confidence_endpoint(self):
        from main import app
        paths = [r.path for r in app.routes]
        assert "/api/intent-logs/low-confidence" in paths


class TestIntentLogSchemas:
    """Pydantic 스키마 검증"""

    def test_intent_log_response_fields(self):
        from core.schemas import IntentLogResponse
        fields = set(IntentLogResponse.model_fields.keys())
        expected = {"id", "message", "intent", "confidence", "source", "created_at"}
        assert expected.issubset(fields)

    def test_intent_log_stats_fields(self):
        from core.schemas import IntentLogStats
        fields = set(IntentLogStats.model_fields.keys())
        expected = {"total", "by_source", "by_intent", "low_confidence_count", "avg_confidence"}
        assert expected == fields
