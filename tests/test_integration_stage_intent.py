"""Phase G: StageManager + IntentClassifier 통합 테스트

단위 테스트가 아닌, 실제 SessionState와 함께 두 시스템이
결합되어 동작하는 시나리오를 검증합니다.

테스트 시나리오:
  1. 파이프라인 순방향 진행 중 역방향 복귀 → 상태 초기화 정합성
  2. IntentClassifier Fast-Path → StageManager can_enter 연계
  3. LLM 분류 결과 → StageManager 진입 검증 연계
  4. "진행하고 싶습니다" 키워드 충돌 시나리오 (change_objective vs confirm)
  5. 전체 파이프라인 시뮬레이션 (7단계 순차 진행)
"""
import asyncio
import json
import pytest
from dataclasses import dataclass
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from core.platform.stage_manager import StageManager
from core.platform.intent_classifier import SkillIntentClassifier, IntentResult


# ── 실제 SessionState 대신 사용하는 통합 테스트용 State ──
# SessionState의 모든 파이프라인 관련 필드를 포함
@dataclass
class IntegrationState:
    """SessionState와 동일한 필드 구조 (DB 의존성 없이)"""
    project_id: Optional[str] = None
    file_uploaded: bool = False
    analysis_completed: bool = False
    structural_normalization_done: bool = False
    problem_defined: bool = False
    data_normalized: bool = False
    math_model_confirmed: bool = False
    pre_decision_done: bool = False
    optimization_done: bool = False

    # Optional fields (reset targets)
    last_analysis_report: Optional[str] = None
    csv_summary: Optional[str] = None
    data_facts: Optional[Dict] = None
    data_profile: Optional[Dict] = None
    phase1_summary: Optional[Dict] = None
    problem_definition_proposed: bool = False
    problem_definition: Optional[Dict] = None
    confirmed_problem: Optional[Dict] = None
    constraints_confirmed: bool = False
    confirmed_constraints: Optional[Dict] = None
    clarification_done: bool = False
    clarification_answers: Optional[Dict] = None
    pending_clarifications: Optional[List] = None
    normalization_mapping: Optional[Dict] = None
    normalization_confirmed: bool = False
    normalized_data_summary: Optional[Dict] = None
    math_model: Optional[Dict] = None
    pending_param_inputs: Optional[List] = None
    last_pre_decision_result: Optional[Dict] = None
    solver_selected: Optional[str] = None
    last_optimization_result: Optional[Dict] = None

    # Pending actions
    objective_changing: bool = False
    pending_objective: Optional[Dict] = None
    pending_extra_instructions: Optional[str] = None
    pending_category_change: Optional[Dict] = None


@pytest.fixture
def manager():
    return StageManager()


@pytest.fixture
def classifier():
    return SkillIntentClassifier()


def _make_state_at_stage(stage: str) -> IntegrationState:
    """주어진 단계까지 진행된 상태를 생성하는 헬퍼"""
    flags = {
        "file_uploaded": ["analysis", "structural_normalization", "problem_definition",
                          "data_normalization", "math_model", "pre_decision", "optimization"],
        "analysis_completed": ["structural_normalization", "problem_definition",
                               "data_normalization", "math_model", "pre_decision", "optimization"],
        "structural_normalization_done": ["problem_definition", "data_normalization",
                                          "math_model", "pre_decision", "optimization"],
        "problem_defined": ["data_normalization", "math_model", "pre_decision", "optimization"],
        "data_normalized": ["math_model", "pre_decision", "optimization"],
        "math_model_confirmed": ["pre_decision", "optimization"],
        "pre_decision_done": ["optimization"],
        "optimization_done": [],
    }
    state = IntegrationState()
    for flag, stages in flags.items():
        if stage in stages:
            setattr(state, flag, True)
    return state


# ============================================================
# 1. 파이프라인 순방향 + 역방향 통합
# ============================================================

class TestPipelineForwardBackward:
    """순방향 진행 후 역방향 복귀 시 상태 정합성"""

    def test_full_forward_progression(self, manager):
        """7단계 순차 진행 시뮬레이션"""
        state = IntegrationState(file_uploaded=True)
        stages_order = [
            ("ANALYZE", "analysis", "analysis_completed"),
            ("STRUCTURAL_NORMALIZATION", "structural_normalization", "structural_normalization_done"),
            ("PROBLEM_DEFINITION", "problem_definition", "problem_defined"),
            ("DATA_NORMALIZATION", "data_normalization", "data_normalized"),
            ("MATH_MODEL", "math_model", "math_model_confirmed"),
            ("PRE_DECISION", "pre_decision", "pre_decision_done"),
            ("START_OPTIMIZATION", "optimization", "optimization_done"),
        ]

        for intent, expected_stage, flag in stages_order:
            can, target = manager.can_enter(state, intent)
            assert can is True, f"Should be able to enter {expected_stage}"
            assert target == expected_stage
            assert manager.is_backward(state, intent) is False
            # 단계 완료 시뮬레이션
            setattr(state, flag, True)

        # 모든 단계 완료
        assert manager.current_stage(state) is None
        assert "완료" in manager.get_pipeline_phase_text(state)

    def test_backward_from_solver_to_problem_def(self, manager):
        """솔버 추천 단계에서 문제정의로 역방향 복귀"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            last_analysis_report="분석 결과",
            structural_normalization_done=True,
            phase1_summary={"cols": 5},
            problem_defined=True,
            problem_definition={"objective": "minimize"},
            confirmed_problem={"constraints": []},
            data_normalized=True,
            normalization_mapping={"col1": "mapped"},
            math_model_confirmed=True,
            math_model={"vars": 10},
            pre_decision_done=True,
            last_pre_decision_result={"solver": "cqm"},
        )

        # 역방향 확인
        assert manager.is_backward(state, "PROBLEM_DEFINITION") is True

        # reentry 실행
        reset_fields = manager.prepare_reentry(state, "PROBLEM_DEFINITION")

        # 문제정의 자체 + 이후 단계 초기화
        assert state.problem_defined is False
        assert state.problem_definition is None
        assert state.confirmed_problem is None
        assert state.data_normalized is False
        assert state.normalization_mapping is None
        assert state.math_model is None
        assert state.math_model_confirmed is False
        assert state.pre_decision_done is False
        assert state.last_pre_decision_result is None

        # 이전 단계는 보존
        assert state.analysis_completed is True
        assert state.last_analysis_report == "분석 결과"
        assert state.structural_normalization_done is True
        assert state.phase1_summary == {"cols": 5}

        assert len(reset_fields) > 5

    def test_backward_from_optimization_to_analysis(self, manager):
        """최적화 단계에서 분석으로 역방향 → 거의 모든 상태 초기화"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            last_analysis_report="report",
            csv_summary="summary",
            data_facts={"rows": 100},
            structural_normalization_done=True,
            phase1_summary={"x": 1},
            problem_defined=True,
            problem_definition={"obj": "min"},
            data_normalized=True,
            math_model_confirmed=True,
            math_model={"m": 1},
            pre_decision_done=True,
            optimization_done=True,
            last_optimization_result={"status": "optimal"},
        )

        reset_fields = manager.prepare_reentry(state, "ANALYZE")

        # 분석 자체 + 모든 후속 단계 초기화
        assert state.analysis_completed is False
        assert state.last_analysis_report is None
        assert state.csv_summary is None
        assert state.data_facts is None
        assert state.structural_normalization_done is False
        assert state.problem_defined is False
        assert state.data_normalized is False
        assert state.math_model is None
        assert state.optimization_done is False
        assert state.last_optimization_result is None

        # file_uploaded만 보존
        assert state.file_uploaded is True

    def test_math_model_reentry_preserves_upstream(self, manager):
        """수학모델 재진입 시 문제정의/데이터정규화는 보존"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            problem_definition={"objective": "minimize_cost"},
            confirmed_problem={"constraints": ["c1", "c2"]},
            constraints_confirmed=True,
            confirmed_constraints={"hard": ["c1"]},
            data_normalized=True,
            normalization_mapping={"A": "mapped_A"},
            normalized_data_summary={"rows": 50},
            math_model_confirmed=True,
            math_model={"variables": 100},
            pre_decision_done=True,
            solver_selected="dwave_hybrid_cqm",
            optimization_done=True,
            last_optimization_result={"objective_value": 42},
        )

        manager.prepare_reentry(state, "MATH_MODEL")

        # 수학모델 이후 초기화
        assert state.math_model is None
        assert state.math_model_confirmed is False
        assert state.pre_decision_done is False
        assert state.solver_selected is None
        assert state.optimization_done is False
        assert state.last_optimization_result is None

        # 문제정의 + 데이터정규화 보존
        assert state.problem_defined is True
        assert state.problem_definition == {"objective": "minimize_cost"}
        assert state.confirmed_problem == {"constraints": ["c1", "c2"]}
        assert state.data_normalized is True
        assert state.normalization_mapping == {"A": "mapped_A"}

    def test_forward_skip_blocked(self, manager):
        """필수 조건 미충족 시 건너뛰기 차단"""
        state = IntegrationState(file_uploaded=True)

        # 분석 안됨 → 문제정의 불가
        can, redirect = manager.can_enter(state, "PROBLEM_DEFINITION")
        assert can is False

        # 분석 안됨 → 수학모델 불가
        can, redirect = manager.can_enter(state, "MATH_MODEL")
        assert can is False

        # 분석 안됨 → 최적화 불가
        can, redirect = manager.can_enter(state, "START_OPTIMIZATION")
        assert can is False


# ============================================================
# 2. IntentClassifier + StageManager 연계
# ============================================================

class TestClassifierStageIntegration:
    """IntentClassifier의 결과를 StageManager가 검증하는 연계 흐름"""

    def test_fast_path_confirm_at_problem_def_stage(self, classifier, manager):
        """문제정의 단계에서 '확인' 버튼 → confirm intent → 진입 가능 확인"""
        state = _make_state_at_stage("problem_definition")

        # 1. Fast-Path 분류
        result = classifier.fast_path("problem_definition", "확인")
        assert result is not None
        assert result.intent == "confirm"

        # 2. StageManager: 문제정의 단계 진입 가능 확인
        can, target = manager.can_enter(state, "PROBLEM_DEFINITION")
        assert can is True
        assert target == "problem_definition"

    def test_fast_path_confirm_math_model(self, classifier, manager):
        """수학모델 단계에서 '확정' 버튼 → confirm"""
        state = _make_state_at_stage("math_model")

        result = classifier.fast_path("math_model", "확정")
        assert result is not None
        assert result.intent == "confirm"

        can, target = manager.can_enter(state, "MATH_MODEL")
        assert can is True

    def test_solver_priority_fast_path(self, classifier, manager):
        """솔버 추천 단계에서 버튼 클릭 → 우선순위 intent"""
        state = _make_state_at_stage("pre_decision")

        # 정확도 우선
        result = classifier.fast_path("solver", "정확도 우선으로 솔버 추천해줘")
        assert result is not None
        assert result.intent == "priority_accuracy"

        # 속도 우선
        result = classifier.fast_path("solver", "속도 우선으로 솔버 추천해줘")
        assert result is not None
        assert result.intent == "priority_speed"

        can, _ = manager.can_enter(state, "PRE_DECISION")
        assert can is True

    def test_analyze_reanalyze_fast_path(self, classifier, manager):
        """분석 단계에서 '재분석' → reanalyze intent + 역방향 감지"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
        )

        result = classifier.fast_path("analyze", "재분석")
        assert result is not None
        assert result.intent == "reanalyze"

        # 현재 data_normalization 단계 → ANALYZE는 역방향
        assert manager.is_backward(state, "ANALYZE") is True

    def test_blocked_entry_with_valid_intent(self, classifier, manager):
        """유효한 intent이지만 필수 조건 미충족 → 차단"""
        state = IntegrationState(file_uploaded=True)
        # 분석 완료 안됨 → 문제정의 불가

        result = classifier.fast_path("math_model", "확정")
        assert result is not None
        assert result.intent == "confirm"

        can, redirect = manager.can_enter(state, "MATH_MODEL")
        assert can is False
        # data_normalized 미충족 → redirect

    def test_non_pipeline_intent_always_allowed(self, classifier, manager):
        """파이프라인 외 intent는 항상 허용"""
        state = IntegrationState()  # 아무것도 안됨

        # RESET, GUIDE 등은 StageManager에 등록 안됨
        can, target = manager.can_enter(state, "RESET")
        assert can is True
        assert target is None


# ============================================================
# 3. LLM 분류 + StageManager 연계 (모킹)
# ============================================================

class TestLLMClassifyStageIntegration:
    """LLM 분류 결과를 StageManager와 연계하여 검증"""

    def test_llm_change_objective_not_confirm(self, classifier, manager):
        """핵심 시나리오: '목적함수를 바꾸고 진행하고 싶습니다'
        → LLM이 change_objective로 분류 (confirm이 아님)"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "intent": "change_objective",
            "params": {
                "target_objective": "minimize_crew",
                "extra_instructions": "승무원 수를 최소화"
            },
            "confidence": 0.92,
        })
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition",
            "목적함수를 바꾸고 진행하고 싶습니다",
            state_summary="문제 정의 완료, 제약조건 3개",
        ))

        # 핵심 검증: "진행" 키워드에도 불구하고 change_objective
        assert result.intent == "change_objective"
        assert result.intent != "confirm"
        assert result.confidence > 0.6
        assert result.params.get("target_objective") == "minimize_crew"

    def test_llm_confirm_simple(self, classifier, manager):
        """단순 확인 메시지 → confirm"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"intent": "confirm", "params": {}, "confidence": 0.95}'
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition", "네 좋습니다"
        ))
        assert result.intent == "confirm"

        # StageManager에서 문제정의 진입 확인
        state = _make_state_at_stage("problem_definition")
        can, _ = manager.can_enter(state, "PROBLEM_DEFINITION")
        assert can is True

    def test_llm_low_confidence_fallback(self, classifier):
        """LLM 분류 confidence가 낮으면 fallback"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"intent": "confirm", "params": {}, "confidence": 0.4}'
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition", "뭔가 하고 싶은데"
        ))
        assert result.confidence < classifier.CONFIDENCE_THRESHOLD

    def test_llm_set_parameter_with_extraction(self, classifier):
        """파라미터 추출이 포함된 분류"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "intent": "set_parameter",
            "params": {"param_name": "day_crew_count", "param_value": "32"},
            "confidence": 0.88,
        })
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition", "day_crew_count = 32"
        ))
        assert result.intent == "set_parameter"
        assert result.params["param_name"] == "day_crew_count"
        assert result.params["param_value"] == "32"

    def test_llm_category_change_with_backward_check(self, classifier, manager):
        """카테고리 변경 요청 → 문제정의 단계 역방향 감지"""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "intent": "change_category",
            "params": {"constraint_name": "no_overlap", "target_category": "soft"},
            "confidence": 0.85,
        })
        mock_model.generate_content = MagicMock(return_value=mock_response)

        result = asyncio.run(classifier.classify(
            mock_model, "problem_definition",
            "no_overlap을 soft로 변경해줘"
        ))
        assert result.intent == "change_category"

        # 수학모델 완료 상태에서 문제정의 역방향
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
        )
        assert manager.is_backward(state, "PROBLEM_DEFINITION") is True


# ============================================================
# 4. Cross-Skill 분류 검증
# ============================================================

class TestCrossSkillClassification:
    """여러 스킬에 걸친 Fast-Path / LLM 분류 정합성"""

    def test_all_skills_have_button_actions(self, classifier):
        """모든 스킬에 button_actions가 정의되어 있는지"""
        for skill_name in ["problem_definition", "math_model", "analyze", "solver"]:
            config = classifier.get_skill_config(skill_name)
            assert config is not None, f"{skill_name} config missing"
            assert "button_actions" in config, f"{skill_name} missing button_actions"

    def test_all_skills_fast_path_no_cross_contamination(self, classifier):
        """한 스킬의 버튼이 다른 스킬에서 매칭되지 않는지"""
        # "확정"은 math_model 전용
        assert classifier.fast_path("math_model", "확정") is not None
        assert classifier.fast_path("problem_definition", "확정") is None

        # "재분석"은 analyze 전용
        assert classifier.fast_path("analyze", "재분석") is not None
        assert classifier.fast_path("problem_definition", "재분석") is None

        # "확인"은 problem_definition과 math_model 모두에 있음
        pd_result = classifier.fast_path("problem_definition", "확인")
        mm_result = classifier.fast_path("math_model", "확인")
        assert pd_result is not None
        assert mm_result is not None

    def test_solver_all_priorities(self, classifier):
        """솔버 스킬의 모든 우선순위 버튼 매칭"""
        priorities = {
            "정확도 우선으로 솔버 추천해줘": "priority_accuracy",
            "속도 우선으로 솔버 추천해줘": "priority_speed",
            "비용 우선으로 솔버 추천해줘": "priority_cost",
            "솔버 다시 추천해줘": "auto",
        }
        for button_text, expected_intent in priorities.items():
            result = classifier.fast_path("solver", button_text)
            assert result is not None, f"'{button_text}' should match"
            assert result.intent == expected_intent, (
                f"'{button_text}' → {result.intent}, expected {expected_intent}"
            )

    def test_problem_definition_all_buttons(self, classifier):
        """문제정의 스킬의 모든 버튼 매칭"""
        buttons = {
            "확인": "confirm",
            "승인": "confirm",
            "ok": "confirm",
            "수정": "modify_general",
            "다시 분석": "restart",
            "취소": "cancel",
            "cancel": "cancel",
        }
        for button_text, expected_intent in buttons.items():
            result = classifier.fast_path("problem_definition", button_text)
            assert result is not None, f"'{button_text}' should match"
            assert result.intent == expected_intent


# ============================================================
# 5. 파이프라인 시뮬레이션: 복합 시나리오
# ============================================================

class TestPipelineSimulation:
    """실제 사용 시나리오를 시뮬레이션"""

    def test_scenario_modify_objective_then_proceed(self, classifier, manager):
        """시나리오: 문제정의 확정 → 수학모델 생성 → 목적함수 변경 요청
        → 역방향 문제정의 → 재확정 → 다시 수학모델"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            problem_definition={"objective": "minimize_cost"},
            confirmed_problem={"constraints": ["c1"]},
            data_normalized=True,
            math_model_confirmed=True,
            math_model={"variables": 50},
        )

        # Step 1: 목적함수 변경 요청 → 역방향
        assert manager.is_backward(state, "PROBLEM_DEFINITION") is True
        reset_fields = manager.prepare_reentry(state, "PROBLEM_DEFINITION")
        assert state.math_model is None
        assert state.math_model_confirmed is False
        assert state.problem_defined is False

        # Step 2: 문제정의 재완료
        state.problem_defined = True
        state.problem_definition = {"objective": "minimize_crew"}
        state.confirmed_problem = {"constraints": ["c1", "c2"]}

        # Step 3: 데이터 정규화 재완료
        state.data_normalized = True

        # Step 4: 수학모델 진입 가능
        can, target = manager.can_enter(state, "MATH_MODEL")
        assert can is True
        assert target == "math_model"
        assert manager.is_backward(state, "MATH_MODEL") is False

    def test_scenario_reanalyze_resets_everything(self, classifier, manager):
        """시나리오: 최적화 완료 후 '재분석' → 거의 모든 상태 초기화"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            last_analysis_report="report",
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
            pre_decision_done=True,
            optimization_done=True,
            last_optimization_result={"status": "optimal"},
        )

        # 재분석 Fast-Path
        result = classifier.fast_path("analyze", "재분석")
        assert result is not None
        assert result.intent == "reanalyze"

        # 역방향 확인 + 초기화
        assert manager.is_backward(state, "ANALYZE") is True
        manager.prepare_reentry(state, "ANALYZE")

        # file_uploaded만 남고 전부 초기화
        assert state.file_uploaded is True
        assert state.analysis_completed is False
        assert state.problem_defined is False
        assert state.optimization_done is False

        # 현재 단계 = analysis
        assert manager.current_stage(state) == "analysis"
        assert "분석" in manager.get_pipeline_phase_text(state)

    def test_scenario_solver_reselection(self, classifier, manager):
        """시나리오: 최적화 실행 후 솔버 재추천 → pre_decision 역방향"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
            pre_decision_done=True,
            solver_selected="dwave_hybrid_cqm",
            optimization_done=True,
            last_optimization_result={"status": "feasible"},
        )

        # 솔버 다시 추천 버튼
        result = classifier.fast_path("solver", "솔버 다시 추천해줘")
        assert result.intent == "auto"

        # 역방향: pre_decision
        assert manager.is_backward(state, "PRE_DECISION") is True
        manager.prepare_reentry(state, "PRE_DECISION")

        assert state.pre_decision_done is False
        assert state.solver_selected is None
        assert state.optimization_done is False
        assert state.last_optimization_result is None

        # 수학모델은 보존
        assert state.math_model_confirmed is True

    def test_scenario_pending_objective_state(self, classifier, manager):
        """시나리오: 목적함수 변경 중간 상태 (pending) → 취소 → 원복"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            problem_definition={"objective": "minimize_cost"},
        )

        # 목적함수 변경 시작 → pending 상태 설정
        state.objective_changing = True
        state.pending_objective = {"name": "minimize_crew", "data": {}}
        state.pending_extra_instructions = "승무원 수 최소화로"

        # 취소 Fast-Path
        result = classifier.fast_path("problem_definition", "취소")
        assert result.intent == "cancel"

        # 취소 처리: pending 상태 초기화
        state.objective_changing = False
        state.pending_objective = None
        state.pending_extra_instructions = None

        # 원래 문제정의 보존
        assert state.problem_defined is True
        assert state.problem_definition == {"objective": "minimize_cost"}

    def test_pipeline_phase_text_at_each_stage(self, manager):
        """각 단계에서 phase text가 올바르게 표시"""
        # Phase 0: 파일 미업로드
        state = IntegrationState()
        assert "파일 미업로드" in manager.get_pipeline_phase_text(state)

        # Phase 1: 분석
        state.file_uploaded = True
        assert "분석" in manager.get_pipeline_phase_text(state)

        # Phase 1.5: 구조 정규화
        state.analysis_completed = True
        assert "구조 정규화" in manager.get_pipeline_phase_text(state)

        # Phase 2: 문제 정의
        state.structural_normalization_done = True
        assert "문제 정의" in manager.get_pipeline_phase_text(state)

        # Phase 3: 데이터 정규화
        state.problem_defined = True
        assert "데이터 정규화" in manager.get_pipeline_phase_text(state)

        # Phase 4: 수학 모델
        state.data_normalized = True
        assert "수학 모델" in manager.get_pipeline_phase_text(state)

        # Phase 5: 솔버 추천
        state.math_model_confirmed = True
        assert "솔버 추천" in manager.get_pipeline_phase_text(state)

        # Phase 6: 최적화
        state.pre_decision_done = True
        assert "최적화" in manager.get_pipeline_phase_text(state)

        # Phase 7: 완료
        state.optimization_done = True
        assert "완료" in manager.get_pipeline_phase_text(state)


# ============================================================
# 6. Edge Cases
# ============================================================

class TestEdgeCases:
    """경계 조건 테스트"""

    def test_double_backward_reentry(self, manager):
        """같은 단계로 두 번 역방향 복귀 → 두 번째는 no-op"""
        state = IntegrationState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
            math_model={"v": 1},
        )

        # 첫 번째 역방향
        reset1 = manager.prepare_reentry(state, "PROBLEM_DEFINITION")
        assert len(reset1) > 0

        # 두 번째 역방향 (이미 초기화됨)
        reset2 = manager.prepare_reentry(state, "PROBLEM_DEFINITION")
        # 현재 문제정의 단계이므로 더 이상 역방향이 아님
        assert len(reset2) == 0

    def test_fast_path_empty_message(self, classifier):
        """빈 메시지 → Fast-Path 매칭 안됨"""
        result = classifier.fast_path("problem_definition", "")
        assert result is None

    def test_fast_path_whitespace_only(self, classifier):
        """공백만 있는 메시지 → Fast-Path 매칭 안됨"""
        result = classifier.fast_path("problem_definition", "   ")
        assert result is None

    def test_parse_response_deeply_nested_json(self, classifier):
        """깊은 중첩 JSON 파싱"""
        raw = '{"intent": "change_objective", "params": {"target_objective": "min_crew"}, "confidence": 0.9}'
        result = classifier._parse_response(raw, "problem_definition")
        assert result.intent == "change_objective"
        assert result.params["target_objective"] == "min_crew"

    def test_concurrent_stage_checks(self, manager):
        """여러 intent에 대한 동시 can_enter 검사"""
        state = _make_state_at_stage("math_model")

        results = {}
        for intent in ["ANALYZE", "PROBLEM_DEFINITION", "MATH_MODEL",
                        "PRE_DECISION", "START_OPTIMIZATION"]:
            can, target = manager.can_enter(state, intent)
            results[intent] = (can, target)

        # 분석, 문제정의 → 가능 (역방향)
        assert results["ANALYZE"][0] is True
        assert results["PROBLEM_DEFINITION"][0] is True
        # 수학모델 → 가능 (현재 단계)
        assert results["MATH_MODEL"][0] is True
        # 솔버추천 → 불가 (math_model_confirmed 필요)
        assert results["PRE_DECISION"][0] is False
        # 최적화 → 불가
        assert results["START_OPTIMIZATION"][0] is False
