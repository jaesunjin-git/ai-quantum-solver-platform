"""
domains/crew/agent.py
─────────────────────
CrewAgent 오케스트레이션 모듈.

사용자 메시지를 받아 의도를 분류하고, 적절한 스킬을 실행하여 응답을 반환합니다.

구조:
  - CrewAgent: 메인 에이전트 클래스
    - run(): 진입점 (세션 로드 → _run_inner)
    - _run_inner(): 의도 분류 → 스킬 실행 흐름
    - _llm_select_and_execute(): LLM 기반 스킬 선택
    - _build_action_history(): 세션 히스토리 → LLM 컨텍스트
    - _execute_skill(): intent → 스킬 함수 라우팅

분리된 모듈:
  - session.py: 세션 상태 관리 (SessionState, CrewSession)
  - classifier.py: 키워드 기반 의도 분류 (InputClassifier)
  - utils.py: 순수 헬퍼 함수
  - skills/: 개별 스킬 함수 패키지
    - analyze.py, math_model.py, solver.py, general.py, handlers.py
"""

from __future__ import annotations

import re
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional
from pathlib import Path

import yaml
import google.generativeai as genai

from core.config import settings


# utils.py에서 분리된 헬퍼 함수 (Step 1 리팩토링)
from domains.crew.utils import (clean_report, build_next_options, error_response)

# session.py에서 분리된 세션 관리 (Step 2 리팩토링)
from domains.crew.session import (
    SessionState, CrewSession,
    save_session_state, load_session_state,
    get_session, _restore_history_from_db
)

# classifier.py에서 분리된 의도 분류기 (Step 3 리팩토링)
from domains.crew.classifier import (
    InputClassifier, SKILL_TO_INTENT, parse_skill_from_llm
)

from core.platform.intent_classifier import IntentResult, log_intent

# skills/ 패키지에서 분리된 스킬 함수 (Step 4 리팩토링)
from domains.crew.skills.problem_definition import skill_problem_definition
from domains.crew.skills.data_normalization import skill_data_normalization
from domains.crew.skills.structural_normalization import skill_structural_normalization
from domains.crew.skills.analyze import skill_analyze, skill_show_analysis
from domains.crew.skills.general import skill_answer, skill_general, skill_ask_for_data
from domains.crew.skills.math_model import skill_math_model, skill_show_math_model, handle_math_model_confirm
from domains.crew.skills.solver import skill_pre_decision, skill_start_optimization, skill_show_solver, skill_show_opt_result
from domains.crew.skills.handlers import handle_file_upload, handle_reset, handle_guide, handle_domain_change

logger = logging.getLogger(__name__)


# ============================================================
# ============================================================
# 5. CrewAgent
# ============================================================

class CrewAgent:
    def __init__(self):
        try:
            genai.configure(api_key=settings.GOOGLE_API_KEY)
            self._system_prompt = self._load_system_prompt()
            self.model = genai.GenerativeModel(
                model_name=settings.MODEL_ANALYSIS,
                system_instruction=self._system_prompt,
            )
            logger.info(f"CrewAgent initialized: {settings.MODEL_ANALYSIS}")
        except Exception as e:
            logger.error(f"CrewAgent init failed: {e}")
            self.model = None

    def _load_system_prompt(self) -> str:
        path = Path(__file__).parents[2] / "prompts" / "system.md"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("system.md not found")
            return "You are a crew scheduling optimization assistant."

    def _load_skill_selection_prompt(self) -> str:
        path = Path(__file__).parents[2] / "prompts" / "skill_selection.md"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("skill_selection.md not found, using inline fallback")
            return (
                "{state_block}\n{action_history}\n[USER MESSAGE]\n{message}\n\n"
                "[CURRENT TAB] {current_tab}\n\n"
                "사용 가능한 Skill: AnalyzeDataSkill, MathModelSkill, PreDecisionSkill, "
                "StartOptimizationSkill, ShowResultSkill, AnswerQuestionSkill, GeneralReplySkill\n\n"
                "질문형 메시지는 반드시 AnswerQuestionSkill을 선택하세요.\n"
                "JSON만 출력: {{\"skill\": \"스킬명\", \"parameters\": {{}}}}"
            )

    # ----------------------------------------------------------
    # 메인 라우터
    # ----------------------------------------------------------
    async def run(
        self,
        message: str,
        project_id: str,
        has_file: bool = False,
        event_type: Optional[str] = None,
        event_data: Optional[Dict] = None,
        current_tab: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = get_session(project_id)

        async with session.lock:
            result = await self._run_inner(session, message, project_id, has_file, event_type, event_data, current_tab)
            # 모든 응답 후 세션 상태를 DB에 저장
            save_session_state(project_id, session.state)
            return result

    async def _run_inner(
        self,
        session: CrewSession,
        message: str,
        project_id: str,
        has_file: bool = False,
        event_type: Optional[str] = None,
        event_data: Optional[Dict] = None,
        current_tab: Optional[str] = None,
    ) -> Dict[str, Any]:
            from core.platform.stage_manager import get_stage_manager
            stage_mgr = get_stage_manager()

            # ── 이벤트 기반 ──
            if event_type == "file_upload":
                return await handle_file_upload(session, project_id, event_data)

            if event_type == "problem_definition_confirm":
                return await skill_problem_definition(
                    self.model, session, project_id, "",
                    {"event_type": event_type, "event_data": event_data or {}},
                )

            # ── 1차: 키워드 빠른 우선분류 ──
            quick_intent = InputClassifier.quick_classify(message, has_file=has_file, current_tab=current_tab)
            _cur_stage = stage_mgr.current_stage(session.state)

            if quick_intent:
                logger.info(f"[{project_id}] quick_intent={quick_intent}")
                log_intent(
                    project_id, message,
                    IntentResult(intent=quick_intent, confidence=1.0, source="quick_classify"),
                    pipeline_stage=_cur_stage,
                )
                session.history.append({"role": "user", "content": message})

                # 비파이프라인 intent (RESET, GUIDE 등)는 바로 실행
                direct_handlers = {
                    "RESET": handle_reset,
                    "GUIDE": handle_guide,
                    "DOMAIN_CHANGE": handle_domain_change,
                    "FILE_UPLOAD": handle_file_upload,
                }
                if quick_intent in direct_handlers:
                    if quick_intent == "FILE_UPLOAD":
                        return await direct_handlers[quick_intent](session, project_id, event_data)
                    return await direct_handlers[quick_intent](session, project_id, message)

                # ★ Action 스킬에 대해서도 질문 패턴이면 LLM으로 위임
                _action_intents = {
                    "MATH_MODEL", "ANALYZE", "PRE_DECISION", "START_OPTIMIZATION",
                    "PROBLEM_DEFINITION", "DATA_NORMALIZATION", "STRUCTURAL_NORMALIZATION",
                }
                if quick_intent in _action_intents:
                    _qmarkers, _amarkers = InputClassifier.get_question_guard_config()
                    _msg_q = message.lower().strip()
                    if (any(m in _msg_q for m in _qmarkers)
                            and not any(a in _msg_q for a in _amarkers)):
                        logger.info(
                            f"[{project_id}] Question override: quick_intent={quick_intent} → LLM"
                        )
                        return await self._llm_select_and_execute(session, project_id, message, current_tab)

                # ── StageManager: 진입 가능 여부 + 역방향 초기화 ──
                intent_to_route = self._apply_stage_transition(
                    stage_mgr, session, project_id, quick_intent
                )
                return await self._execute_skill(session, project_id, intent_to_route, message, {})

            # ── 2차: 질문 감지 → LLM 직접 위임 (파이프라인 리다이렉트 건너뜀) ──
            _qmarkers, _amarkers = InputClassifier.get_question_guard_config()
            _msg_q = message.lower().strip()
            _is_question = (
                any(m in _msg_q for m in _qmarkers)
                or _msg_q.endswith("?")
                or any(_msg_q.endswith(e) for e in InputClassifier._question_endings)
            )
            if _is_question and not any(a in _msg_q for a in _amarkers):
                logger.info(f"[{project_id}] Question detected → LLM")
                session.history.append({"role": "user", "content": message})
                return await self._llm_select_and_execute(session, project_id, message, current_tab)

            # ── 3차: 파이프라인 순방향 자동 진행 ──
            # 현재 미완료된 가장 낮은 단계로 리다이렉트
            next_stage = stage_mgr.current_stage(session.state)
            if next_stage:
                stage_def = stage_mgr.get_stage_info(next_stage)
                if stage_def:
                    # 해당 단계의 첫 번째 intent_code로 라우팅
                    redirect_intent = stage_def["intent_codes"][0]
                    logger.info(
                        f"[{project_id}] Pipeline auto-redirect → {redirect_intent} "
                        f"(stage={next_stage})"
                    )
                    return await self._execute_skill(session, project_id, redirect_intent, message, {})

            # ── 4차: LLM 스킬 선택 (모든 파이프라인 완료 후) ──
            logger.info(f"[{project_id}] → LLM skill selection")
            session.history.append({"role": "user", "content": message})
            return await self._llm_select_and_execute(session, project_id, message, current_tab)

    # ----------------------------------------------------------
    # Stage Transition: 진입 가능 여부 + 역방향 초기화
    # ----------------------------------------------------------
    def _apply_stage_transition(
        self, stage_mgr, session: CrewSession, project_id: str, intent: str
    ) -> str:
        """
        StageManager를 사용하여:
        1. 역방향이면 후속 상태 초기화
        2. 진입 불가능하면 리다이렉트 intent 반환
        """
        state = session.state

        # 역방향 복귀 감지 → 자동 초기화
        if stage_mgr.is_backward(state, intent):
            reset_fields = stage_mgr.prepare_reentry(state, intent)
            if reset_fields:
                logger.info(
                    f"[{project_id}] Backward reentry: {intent} — "
                    f"reset {len(reset_fields)} fields"
                )
                save_session_state(project_id, state)

        # 진입 가능 여부 확인
        can_enter, target_or_redirect = stage_mgr.can_enter(state, intent)
        if not can_enter and target_or_redirect:
            logger.info(
                f"[{project_id}] Stage guard: {intent} blocked → {target_or_redirect}"
            )
            # 리다이렉트 단계의 첫 번째 intent_code
            redirect_def = stage_mgr.get_stage_info(target_or_redirect)
            if redirect_def:
                return redirect_def["intent_codes"][0]

        return intent

    # ----------------------------------------------------------
    # LLM Skill 선택 → 파싱 → 실행
    # ----------------------------------------------------------
    async def _llm_select_and_execute(
        self, session: CrewSession, project_id: str, message: str, current_tab: Optional[str] = None
    ) -> Dict:
        if not self.model:
            return error_response("AI 모델에 연결할 수 없습니다.")

        try:
            state = session.state

            # 작업 이력 요약 생성
            action_history = self._build_action_history(session)

            # 현재 파이프라인 단계 계산
            pipeline_phase = self._get_pipeline_phase(state)

            # prompts/skill_selection.md 로드 후 변수 치환
            prompt_template = self._load_skill_selection_prompt()
            prompt = prompt_template.format(
                state_block=state.to_state_block(),
                action_history=action_history,
                message=message,
                current_tab=current_tab or "none",
                pipeline_phase=pipeline_phase,
            )

            response = await asyncio.to_thread(
                self.model.generate_content, prompt
            )
            llm_text = response.text.strip()
            logger.info(f"[{project_id}] LLM response: {llm_text[:200]}")

            # JSON 파싱 시도
            intent, parameters = parse_skill_from_llm(llm_text)

            if intent:
                logger.info(f"[{project_id}] LLM selected: {intent}")
                log_intent(
                    project_id, message,
                    IntentResult(intent=intent, params=parameters or {}, confidence=0.8, source="llm_skill_select"),
                    pipeline_stage=stage_mgr.current_stage(session.state) if hasattr(self, '_run_inner') else None,
                )
                return await self._execute_skill(session, project_id, intent, message, parameters)

            # JSON 파싱 실패 → 텍스트에서 스킬명 감지 시도
            skill_name_map = {
                "AnswerQuestionSkill": "ANSWER",
                "GeneralReplySkill": "GENERAL",
                "AnalyzeDataSkill": "ANALYZE",
                "ProblemDefinitionSkill": "PROBLEM_DEFINITION",
                "DataNormalizationSkill": "DATA_NORMALIZATION",
                "PreDecisionSkill": "PRE_DECISION",
                "MathModelSkill": "MATH_MODEL",
                "StartOptimizationSkill": "START_OPTIMIZATION",
                "ShowResultSkill": "SHOW_RESULT",
                "FileReceivedSkill": "GENERAL",
            }
            for skill_name, mapped_intent in skill_name_map.items():
                if skill_name in llm_text:
                    logger.info(f"[{project_id}] Detected skill name in text: {skill_name} → {mapped_intent}")
                    return await self._execute_skill(session, project_id, mapped_intent, message, {})

            # 스킬명도 없으면 → 내부 지시문/JSON이 아닌 자연어 응답인지 확인
            cleaned = clean_report(llm_text)
            if cleaned and len(cleaned) > 20 and not cleaned.startswith("{"):
                return {
                    "type": "text",
                    "text": cleaned,
                    "data": None,
                    "options": build_next_options(session.state)
                }

            # 그 외 모든 경우 → _skill_answer로 직접 답변 생성
            logger.info(f"[{project_id}] Fallback to _skill_answer")
            return await skill_answer(self.model, self._build_action_history, session, project_id, message, {})

        except Exception as e:
            logger.error(f"LLM skill selection failed: {e}", exc_info=True)
            return error_response("요청 처리 중 오류가 발생했습니다.")

    # ----------------------------------------------------------
    # 작업 이력 요약 생성
    # ----------------------------------------------------------
    def _build_action_history(self, session: CrewSession) -> str:
        """session.history에서 구조화된 작업 이력을 추출"""
        if not session.history:
            return ""

        action_lines = ["[ACTION HISTORY - 최근 작업 이력]"]
        action_count = 0

        for entry in session.history:
            role = entry.get("role", "")
            content_text = entry.get("content", "")
            action_type = entry.get("action_type", "")

            if role == "user" and content_text:
                action_lines.append(f"  User: {content_text[:100]}")
                action_count += 1
            elif role == "assistant" and action_type:
                action_map = {
                    "file_uploaded": "파일 업로드 완료",
                    "report": "데이터 분석 완료",
                    "problem_definition": "문제 정의",
                    "problem_defined": "문제 정의 확정",
                    "normalization": "데이터 정규화",
                    "normalization_complete": "정규화 완료",
                    "math_model": "수학 모델 생성",
                    "solver": "솔버 추천 완료",
                    "result": "최적화 실행 완료",
                }
                action_desc = action_map.get(action_type, action_type)
                action_lines.append(f"  System: {action_desc}")
                action_count += 1
            elif role == "assistant" and content_text and len(content_text) > 10:
                # 일반 응답은 앞부분만
                action_lines.append(f"  Assistant: {content_text[:80]}")
                action_count += 1

        if action_count == 0:
            return ""

        # 최근 10개만 유지
        if len(action_lines) > 11:  # header + 10 entries
            action_lines = action_lines[:1] + action_lines[-10:]

        return "\n".join(action_lines)

    # ----------------------------------------------------------
    # 현재 파이프라인 단계 텍스트 생성
    # ----------------------------------------------------------
    def _get_pipeline_phase(self, state: SessionState) -> str:
        """현재 파이프라인 단계를 사람이 읽기 쉬운 문자열로 반환"""
        from core.platform.stage_manager import get_stage_manager
        return get_stage_manager().get_pipeline_phase_text(state)

    # ----------------------------------------------------------
    # Skill 실행 디스패처
    # ----------------------------------------------------------
    async def _execute_skill(
        self,
        session: CrewSession,
        project_id: str,
        intent: str,
        message: str,
        parameters: Dict,
    ) -> Dict:
        """intent에 따라 해당 Skill 핸들러를 실행"""
        handlers = {
            "ANALYZE": lambda s, p, m, pr: skill_analyze(self.model, s, p, m, pr),
            "STRUCTURAL_NORMALIZATION": lambda s, p, m, pr: skill_structural_normalization(s, p, m, pr),
            "PROBLEM_DEFINITION": lambda s, p, m, pr: skill_problem_definition(self.model, s, p, m, pr),
            "DATA_NORMALIZATION": lambda s, p, m, pr: skill_data_normalization(self.model, s, p, m, pr),
            "SHOW_ANALYSIS": skill_show_analysis,
            "PRE_DECISION": skill_pre_decision,
            "SHOW_MATH_MODEL": skill_show_math_model,
            "MATH_MODEL": lambda s, p, m, pr: skill_math_model(self.model, s, p, m, pr),
            "START_OPTIMIZATION": skill_start_optimization,
            "SHOW_RESULT": skill_show_analysis,
            "SHOW_SOLVER": skill_show_solver,
            "SHOW_OPT_RESULT": skill_show_opt_result,
            "ANSWER": lambda s, p, m, pr: skill_answer(self.model, self._build_action_history, s, p, m, pr),
            "GENERAL": lambda s, p, m, pr: skill_general(self.model, s, p, m, pr),
            "UPDATE_WORKSPACE": lambda s, p, m, pr: skill_general(self.model, s, p, m, pr),
            "ASK_FOR_DATA": skill_ask_for_data,
        }

        handler = handlers.get(intent, lambda s, p, m, pr: skill_general(self.model, s, p, m, pr))
        result = await handler(session, project_id, message, parameters)

        # Action intent 처리 후 target_tab 추가 (프론트엔드 자동 탭 전환용)
        intent_to_tab = {
            "ANALYZE": "analysis",
            "STRUCTURAL_NORMALIZATION": "analysis",
            "PROBLEM_DEFINITION": "analysis",
            "DATA_NORMALIZATION": "analysis",
            "SHOW_ANALYSIS": "analysis",
            "MATH_MODEL": "math_model",
            "SHOW_MATH_MODEL": "math_model",
            "PRE_DECISION": "solver",
            "SHOW_SOLVER": "solver",
            "START_OPTIMIZATION": "result",
            "SHOW_OPT_RESULT": "result",
        }
        if isinstance(result, dict) and intent in intent_to_tab:
            result_type = result.get("type", "")
            # 오류/경고 시에는 target_tab을 설정하지 않음 (현재 탭 유지)
            if result_type in ("error", "warning"):
                pass
            else:
                if result.get("data") is None:
                    result["data"] = {}
                if isinstance(result.get("data"), dict):
                    result["data"]["target_tab"] = intent_to_tab[intent]

        return result

    # ----------------------------------------------------------
    # Skill: FileReceived
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: AnalyzeData
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: ShowMathModel
    # ----------------------------------------------------------

    
    # ----------------------------------------------------------
    # Skill: ShowAnalysis (캐시 결과)
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: PreDecision (솔버 추천)
    # ----------------------------------------------------------

    
    # ----------------------------------------------------------
    # Skill: MathModel (수학 모델 생성)
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: MathModel 확정/재생성 처리
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: StartOptimization
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: ShowResult / ShowSolver / ShowOptResult
    # ----------------------------------------------------------


    # ----------------------------------------------------------
    # Skill: AnswerQuestion
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # Skill: GeneralReply / AskForData
    # ----------------------------------------------------------


    # ----------------------------------------------------------
    # 특수 핸들러: 리셋 / 가이드 / 도메인 변경
    # ----------------------------------------------------------


    # ----------------------------------------------------------
    # 유틸리티
    # ----------------------------------------------------------


# ============================================================
# 싱글턴
# ============================================================
crew_agent = CrewAgent()
