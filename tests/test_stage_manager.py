"""StageManager 단위 테스트

configs/pipeline.yaml 기반 파이프라인 단계 전이 로직을 검증합니다.
"""
import pytest
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from core.platform.stage_manager import StageManager, _load_pipeline_config


# ── 테스트용 간이 State ──
@dataclass
class MockState:
    file_uploaded: bool = False
    analysis_completed: bool = False
    structural_normalization_done: bool = False
    problem_defined: bool = False
    data_normalized: bool = False
    math_model_confirmed: bool = False
    pre_decision_done: bool = False
    optimization_done: bool = False
    # Optional fields (reset 대상)
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


@pytest.fixture
def manager():
    return StageManager()


class TestPipelineConfigLoad:
    """pipeline.yaml 로드 검증"""

    def test_config_loads(self):
        config = _load_pipeline_config()
        assert "stages" in config
        assert len(config["stages"]) == 7

    def test_all_stages_have_required_keys(self):
        config = _load_pipeline_config()
        for name, sdef in config["stages"].items():
            assert "order" in sdef, f"{name} missing 'order'"
            assert "intent_codes" in sdef, f"{name} missing 'intent_codes'"
            assert "requires" in sdef, f"{name} missing 'requires'"
            assert "state_flag" in sdef, f"{name} missing 'state_flag'"

    def test_orders_are_sequential(self):
        config = _load_pipeline_config()
        orders = [sdef["order"] for sdef in config["stages"].values()]
        assert orders == sorted(orders)
        assert len(set(orders)) == len(orders)  # 중복 없음


class TestStageForIntent:
    """intent → stage 매핑 검증"""

    def test_analyze_maps_to_analysis(self, manager):
        assert manager.stage_for_intent("ANALYZE") == "analysis"

    def test_show_analysis_maps_to_analysis(self, manager):
        assert manager.stage_for_intent("SHOW_ANALYSIS") == "analysis"

    def test_problem_definition_maps(self, manager):
        assert manager.stage_for_intent("PROBLEM_DEFINITION") == "problem_definition"

    def test_math_model_maps(self, manager):
        assert manager.stage_for_intent("MATH_MODEL") == "math_model"

    def test_start_optimization_maps(self, manager):
        assert manager.stage_for_intent("START_OPTIMIZATION") == "optimization"

    def test_unknown_intent_returns_none(self, manager):
        assert manager.stage_for_intent("RESET") is None
        assert manager.stage_for_intent("GUIDE") is None
        assert manager.stage_for_intent("GENERAL") is None
        assert manager.stage_for_intent("ANSWER") is None


class TestCurrentStage:
    """현재 진행 단계 판단"""

    def test_nothing_done(self, manager):
        state = MockState(file_uploaded=True)
        assert manager.current_stage(state) == "analysis"

    def test_analysis_done(self, manager):
        state = MockState(file_uploaded=True, analysis_completed=True)
        assert manager.current_stage(state) == "structural_normalization"

    def test_up_to_problem_definition(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
        )
        assert manager.current_stage(state) == "problem_definition"

    def test_all_complete(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
            pre_decision_done=True,
            optimization_done=True,
        )
        assert manager.current_stage(state) is None


class TestCanEnter:
    """단계 진입 가능 여부"""

    def test_can_enter_analysis_with_file(self, manager):
        state = MockState(file_uploaded=True)
        can, target = manager.can_enter(state, "ANALYZE")
        assert can is True
        assert target == "analysis"

    def test_cannot_enter_analysis_without_file(self, manager):
        state = MockState()
        can, redirect = manager.can_enter(state, "ANALYZE")
        assert can is False
        # redirect는 file_uploaded를 완료하는 단계 (없음 → None)
        # file_uploaded는 어떤 stage_flag에도 해당하지 않으므로 None
        assert redirect is None

    def test_cannot_enter_problem_def_without_structural_norm(self, manager):
        state = MockState(file_uploaded=True, analysis_completed=True)
        can, redirect = manager.can_enter(state, "PROBLEM_DEFINITION")
        assert can is False
        assert redirect == "structural_normalization"

    def test_can_enter_problem_def_with_structural_norm(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
        )
        can, target = manager.can_enter(state, "PROBLEM_DEFINITION")
        assert can is True
        assert target == "problem_definition"

    def test_cannot_enter_math_model_without_data_norm(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
        )
        can, redirect = manager.can_enter(state, "MATH_MODEL")
        assert can is False
        assert redirect == "data_normalization"

    def test_non_pipeline_intent_always_enters(self, manager):
        state = MockState()
        can, target = manager.can_enter(state, "RESET")
        assert can is True
        assert target is None

    def test_general_always_enters(self, manager):
        state = MockState()
        can, target = manager.can_enter(state, "GENERAL")
        assert can is True
        assert target is None


class TestIsBackward:
    """역방향 감지"""

    def test_forward_not_backward(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
        )
        # 현재 problem_definition (order 3), MATH_MODEL (order 5) → 순방향
        assert manager.is_backward(state, "MATH_MODEL") is False

    def test_backward_detected(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
        )
        # 현재 pre_decision (order 6), PROBLEM_DEFINITION (order 3) → 역방향
        assert manager.is_backward(state, "PROBLEM_DEFINITION") is True

    def test_same_stage_not_backward(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
        )
        # 현재 problem_definition (order 3), PROBLEM_DEFINITION (order 3) → 같은 단계
        assert manager.is_backward(state, "PROBLEM_DEFINITION") is False

    def test_non_pipeline_not_backward(self, manager):
        state = MockState()
        assert manager.is_backward(state, "RESET") is False


class TestPrepareReentry:
    """역방향 복귀 시 상태 초기화"""

    def test_backward_reentry_resets_downstream(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model={"some": "model"},
            math_model_confirmed=True,
            pre_decision_done=True,
            optimization_done=True,
        )
        reset_fields = manager.prepare_reentry(state, "PROBLEM_DEFINITION")

        # problem_definition 이후 단계 초기화 확인
        assert state.data_normalized is False
        assert state.math_model is None
        assert state.math_model_confirmed is False
        assert state.pre_decision_done is False
        assert state.optimization_done is False
        # problem_definition 자체도 초기화 (재시작이므로)
        assert state.problem_defined is False
        assert len(reset_fields) > 0

    def test_forward_reentry_no_reset(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
        )
        # structural_normalization은 순방향 — 초기화 없음
        reset_fields = manager.prepare_reentry(state, "STRUCTURAL_NORMALIZATION")
        assert reset_fields == []

    def test_non_pipeline_no_reset(self, manager):
        state = MockState(file_uploaded=True, analysis_completed=True)
        reset_fields = manager.prepare_reentry(state, "RESET")
        assert reset_fields == []

    def test_analysis_reentry_resets_everything(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            last_analysis_report="report",
            structural_normalization_done=True,
            phase1_summary={"some": "data"},
            problem_defined=True,
            problem_definition={"obj": "min"},
            data_normalized=True,
            math_model_confirmed=True,
            math_model={"m": 1},
            pre_decision_done=True,
            optimization_done=True,
        )
        reset_fields = manager.prepare_reentry(state, "ANALYZE")

        assert state.analysis_completed is False
        assert state.last_analysis_report is None
        assert state.structural_normalization_done is False
        assert state.phase1_summary is None
        assert state.problem_defined is False
        assert state.problem_definition is None
        assert state.data_normalized is False
        assert state.math_model is None
        assert state.optimization_done is False

    def test_math_model_reentry_preserves_problem_def(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            problem_definition={"obj": "min"},
            data_normalized=True,
            math_model_confirmed=True,
            math_model={"m": 1},
            pre_decision_done=True,
            last_pre_decision_result={"solver": "cqm"},
        )
        manager.prepare_reentry(state, "MATH_MODEL")

        # 문제 정의는 보존
        assert state.problem_defined is True
        assert state.problem_definition == {"obj": "min"}
        assert state.data_normalized is True
        # 수학모델 이후는 초기화
        assert state.math_model is None
        assert state.math_model_confirmed is False
        assert state.pre_decision_done is False
        assert state.last_pre_decision_result is None


class TestGetPipelinePhaseText:
    """파이프라인 단계 텍스트 표시"""

    def test_no_file(self, manager):
        state = MockState()
        text = manager.get_pipeline_phase_text(state)
        assert "파일 미업로드" in text

    def test_analysis_phase(self, manager):
        state = MockState(file_uploaded=True)
        text = manager.get_pipeline_phase_text(state)
        assert "분석" in text

    def test_all_complete(self, manager):
        state = MockState(
            file_uploaded=True,
            analysis_completed=True,
            structural_normalization_done=True,
            problem_defined=True,
            data_normalized=True,
            math_model_confirmed=True,
            pre_decision_done=True,
            optimization_done=True,
        )
        text = manager.get_pipeline_phase_text(state)
        assert "완료" in text
