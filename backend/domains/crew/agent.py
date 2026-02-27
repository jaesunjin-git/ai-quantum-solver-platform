# ============================================================
# domains/crew/agent.py — v6.0 (Agentic Skill 구조 복원)
# ============================================================
# 변경 이력:
#   v5.x : InputClassifier 키워드 직접 라우팅
#   v6.0 :
#     - LLM 기반 Skill 선택 복원 (system.md 활용)
#     - InputClassifier는 명확한 키워드 매칭 시 빠른 라우팅 (LLM 비용 절감)
#     - 애매한 입력은 LLM에게 Skill 선택 위임
#     - LLM 응답 JSON 파싱 → Skill별 핸들러 실행
#     - _clean_report로 내부 지시문 제거
# ============================================================

from __future__ import annotations

import re
import json
import logging
import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

import yaml
import google.generativeai as genai

from core.config import settings
from engine.file_service import analyze_csv_summary
from engine.pre_decision import run_pre_decision_analysis
from core.version import create_dataset_version, create_model_version
from engine.math_model_generator import generate_math_model, summarize_model
from utils.prompt_builder import build_analysis_prompt

import json as _json
from core.database import SessionLocal
from core.models import SessionStateDB

logger = logging.getLogger(__name__)


# ============================================================
# 1. 세션 상태
# ============================================================

@dataclass
class SessionState:
    """프로젝트별 워크플로 상태"""
    file_uploaded: bool = False
    analysis_completed: bool = False
    pre_decision_done: bool = False
    solver_selected: Optional[str] = None
    optimization_done: bool = False

    # Version pointers
    current_dataset_version_id: Optional[int] = None
    current_model_version_id: Optional[int] = None
    current_run_id: Optional[int] = None

    detected_domain: Optional[str] = None
    domain_confidence: float = 0.0
    domain_override: Optional[str] = None

    uploaded_files: List[str] = field(default_factory=list)
    last_analysis_report: Optional[str] = None
    last_pre_decision_result: Optional[Dict] = None
    last_optimization_result: Optional[Dict] = None
    last_executed_skill: Optional[str] = None
    csv_summary: Optional[str] = None
    math_model: Optional[Dict] = None
    math_model_confirmed: bool = False
    last_report: Optional[str] = None
    data_facts: Optional[Dict] = None  # ★ 추가: 코드로 계산된 정확한 팩트 데이터 

    def reset_from_math_model(self):
        """수학 모델 재생성 시 후속 단계 모두 초기화"""
        self.math_model = None
        self.math_model_confirmed = False
        self.pre_decision_done = False
        self.last_pre_decision_result = None
        self.solver_selected = None
        self.optimization_done = False
        self.last_optimization_result = None

    def reset_from_analysis(self):
        """데이터 재분석 시 후속 단계 모두 초기화"""
        self.analysis_completed = False
        self.last_analysis_report = None
        self.csv_summary = None
        self.data_facts = None
        self.reset_from_math_model()

    def context_string(self) -> str:
        parts = []
        if self.file_uploaded:
            parts.append(f"files_uploaded=true, files={self.uploaded_files}")
        else:
            parts.append("files_uploaded=false")
        if self.detected_domain:
            try:
                conf = float(self.domain_confidence) if self.domain_confidence else 0.0
                parts.append(f"domain={self.detected_domain}({conf:.0%})")
            except (ValueError, TypeError):
                parts.append(f"domain={self.detected_domain}({self.domain_confidence})")
        parts.append(f"analysis_completed={self.analysis_completed}")
        parts.append(f"pre_decision_done={self.pre_decision_done}")
        if self.solver_selected:
            parts.append(f"solver_selected={self.solver_selected}")
        parts.append(f"optimization_done={self.optimization_done}")
        return " | ".join(parts)

    def to_state_block(self) -> str:
        """LLM에게 전달할 풍부한 상태 블록"""
        lines = ["[CURRENT STATE]"]

        # 1. 파일 업로드 상태
        lines.append(f"files_uploaded: {self.file_uploaded}")
        if self.uploaded_files:
            lines.append(f"  uploaded_files: {self.uploaded_files}")

        # 2. 도메인 정보
        if self.detected_domain:
            lines.append(f"  domain: {self.detected_domain} (confidence: {self.domain_confidence})")

        # 3. 분석 상태 + 요약
        lines.append(f"analysis_completed: {self.analysis_completed}")
        if self.analysis_completed and self.last_analysis_report:
            preview = self.last_analysis_report[:400].replace("\n", " ").strip()
            lines.append(f"  analysis_summary: {preview}")

        # 4. 수학 모델 상태 + 핵심 정보
        lines.append(f"math_model_confirmed: {self.math_model_confirmed}")
        if self.math_model and isinstance(self.math_model, dict):
            try:
                obj = self.math_model.get("objective", {})
                if isinstance(obj, dict):
                    obj_type = obj.get("type", "minimize")
                    obj_desc = obj.get("description", "unknown")
                else:
                    obj_type = "unknown"
                    obj_desc = str(obj)[:100]
                meta = self.math_model.get("metadata", {})
                var_count = meta.get("estimated_variable_count", "?") if isinstance(meta, dict) else "?"
                constraints = self.math_model.get("constraints", [])
                con_count = len(constraints) if isinstance(constraints, list) else "?"
                var_list = self.math_model.get("variables", [])
                var_names = [v.get("id", "?") for v in var_list[:5]] if isinstance(var_list, list) else []
                lines.append(f"  model_objective: {obj_type} - {obj_desc}")
                lines.append(f"  model_variables: {var_count} (names: {var_names})")
                lines.append(f"  model_constraints: {con_count}")
                # 목적함수 alternatives
                alts = obj.get("alternatives", []) if isinstance(obj, dict) else []
                if alts:
                    alt_descs = [a.get("description", "?")[:50] for a in alts[:3] if isinstance(a, dict)]
                    lines.append(f"  alternative_objectives: {alt_descs}")
            except Exception:
                lines.append("  model_info: exists but parse error")

        # 5. 솔버 추천 상태
        lines.append(f"pre_decision_done: {self.pre_decision_done}")
        lines.append(f"solver_selected: {self.solver_selected or 'null'}")

        # 6. 최적화 결과 상태 + 요약
        lines.append(f"optimization_done: {self.optimization_done}")
        if self.optimization_done and self.last_optimization_result:
            try:
                r = self.last_optimization_result if isinstance(self.last_optimization_result, dict) else {}
                status = r.get("status", "unknown")
                obj_val = r.get("objective_value", "?")
                solver = r.get("solver_name", "unknown")
                timing = r.get("timing", {})
                total_sec = timing.get("total_sec", "?") if isinstance(timing, dict) else "?"
                m_stats = r.get("model_stats", {})
                total_vars = m_stats.get("total_variables", "?") if isinstance(m_stats, dict) else "?"
                total_cons = m_stats.get("total_constraints", "?") if isinstance(m_stats, dict) else "?"
                lines.append(f"  result_status: {status}")
                lines.append(f"  result_objective: {obj_val}")
                lines.append(f"  result_solver: {solver}")
                lines.append(f"  result_time: {total_sec}s")
                lines.append(f"  result_model_size: {total_vars} vars, {total_cons} constraints")
                warnings = r.get("compile_warnings", [])
                if warnings:
                    lines.append(f"  compile_warnings: {len(warnings)} issues")
            except Exception:
                lines.append("  result_info: exists but parse error")

        # 7. 버전 정보
        if any([self.current_dataset_version_id, self.current_model_version_id, self.current_run_id]):
            lines.append(f"  versions: dataset={self.current_dataset_version_id}, model={self.current_model_version_id}, run={self.current_run_id}")

        return "\n".join(lines)

def save_session_state(project_id: str, state: SessionState):
    """세션 상태를 DB에 저장"""
    db = SessionLocal()
    try:
        pid = int(project_id)
        row = db.query(SessionStateDB).filter(SessionStateDB.project_id == pid).first()
        if not row:
            row = SessionStateDB(project_id=pid)
            db.add(row)

        # Boolean 상태
        row.file_uploaded = state.file_uploaded
        row.analysis_completed = state.analysis_completed
        row.math_model_confirmed = state.math_model_confirmed
        row.pre_decision_done = state.pre_decision_done
        row.optimization_done = state.optimization_done

        # 텍스트/JSON 데이터
        row.uploaded_files = _json.dumps(state.uploaded_files, ensure_ascii=False) if state.uploaded_files else None
        row.csv_summary = state.csv_summary
        row.last_analysis_report = state.last_analysis_report
        row.math_model = _json.dumps(state.math_model, ensure_ascii=False) if state.math_model else None
        row.last_pre_decision_result = _json.dumps(state.last_pre_decision_result, ensure_ascii=False) if state.last_pre_decision_result else None
        row.last_optimization_result = _json.dumps(state.last_optimization_result, ensure_ascii=False) if state.last_optimization_result else None

         # 팩트 데이터
        row.data_facts = _json.dumps(state.data_facts, ensure_ascii=False) if state.data_facts else None

        # Version pointers
        row.current_dataset_version_id = state.current_dataset_version_id
        row.current_model_version_id = state.current_model_version_id
        row.current_run_id = state.current_run_id

        # 도메인
        row.detected_domain = state.detected_domain
        row.domain_confidence = state.domain_confidence

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save session state for project {project_id}: {e}")
    finally:
        db.close()


def load_session_state(project_id: str) -> Optional[SessionState]:
    """DB에서 세션 상태를 복원"""
    db = SessionLocal()
    try:
        pid = int(project_id)
        row = db.query(SessionStateDB).filter(SessionStateDB.project_id == pid).first()
        if not row:
            return None

        state = SessionState()
        state.file_uploaded = row.file_uploaded or False
        state.analysis_completed = row.analysis_completed or False
        state.math_model_confirmed = row.math_model_confirmed or False
        state.pre_decision_done = row.pre_decision_done or False
        state.optimization_done = row.optimization_done or False

        state.uploaded_files = _json.loads(row.uploaded_files) if row.uploaded_files else []
        state.csv_summary = row.csv_summary
        state.last_analysis_report = row.last_analysis_report
        state.math_model = _json.loads(row.math_model) if row.math_model else None
        state.last_pre_decision_result = _json.loads(row.last_pre_decision_result) if row.last_pre_decision_result else None
        state.last_optimization_result = _json.loads(row.last_optimization_result) if row.last_optimization_result else None

        # 팩트 데이터
        state.data_facts = _json.loads(row.data_facts) if row.data_facts else None

        # Version pointers
        state.current_dataset_version_id = getattr(row, 'current_dataset_version_id', None)
        state.current_model_version_id = getattr(row, 'current_model_version_id', None)
        state.current_run_id = getattr(row, 'current_run_id', None)

        state.detected_domain = row.detected_domain
        try:
            state.domain_confidence = float(row.domain_confidence) if row.domain_confidence else 0.0
        except (ValueError, TypeError):
            state.domain_confidence = 0.0

        return state
    except Exception as e:
        logger.error(f"Failed to load session state for project {project_id}: {e}")
        return None
    finally:
        db.close()


# ============================================================
# 2. 세션 관리
# ============================================================

class CrewSession:
    def __init__(self):
        self.history: deque = deque(maxlen=20)
        self.state = SessionState()
        self.lock = asyncio.Lock()


_sessions: Dict[str, CrewSession] = {}


def _restore_history_from_db(project_id: str, session: CrewSession):
    """DB에서 최근 대화 히스토리를 복원하여 session.history에 로드"""
    try:
        from core.database import SessionLocal
        from core.models import ChatHistoryDB
        db = SessionLocal()
        try:
            pid = int(project_id) if str(project_id).isdigit() else 0
            rows = (
                db.query(ChatHistoryDB)
                .filter(ChatHistoryDB.project_id == pid)
                .order_by(ChatHistoryDB.created_at.desc())
                .limit(20)
                .all()
            )
            rows.reverse()  # 시간순 정렬
            for row in rows:
                entry = {"role": row.role, "content": row.message_text or ""}
                # Action 이력 추출: assistant 응답에서 수행된 작업 기록
                if row.role == "assistant" and row.card_json:
                    try:
                        card = json.loads(row.card_json) if isinstance(row.card_json, str) else row.card_json
                        if isinstance(card, dict):
                            view_mode = card.get("view_mode", "")
                            target_tab = card.get("target_tab", "")
                            entry["action_type"] = view_mode or target_tab
                    except Exception:
                        pass
                session.history.append(entry)
            if rows:
                logger.info(f"[{project_id}] Restored {len(rows)} history entries from DB")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[{project_id}] Failed to restore history: {e}")


def get_session(project_id: str) -> CrewSession:
    if project_id not in _sessions:
        session = CrewSession()
        # DB에서 이전 상태 복원 시도
        saved_state = load_session_state(project_id)
        if saved_state:
            session.state = saved_state
            logger.info(f"[{project_id}] Session state restored from DB")
        # DB에서 대화 히스토리 복원
        _restore_history_from_db(project_id, session)
        _sessions[project_id] = session
    return _sessions[project_id]

# ============================================================
# 3. 입력 분류기 (빠른 라우팅용)
# ============================================================

class InputClassifier:
    _YAML_PATH = Path(__file__).parents[2] / "configs" / "classifier_keywords.yaml"
    _keywords: Dict[str, List[str]] = {}
    _domain_map: Dict[str, str] = {}
    _loaded: bool = False

    @classmethod
    def _load_keywords(cls):
        if cls._loaded:
            return
        try:
            with open(cls._YAML_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            cls._keywords = {k: v for k, v in raw.items() if k != "domain_keyword_map"}
            cls._domain_map = raw.get("domain_keyword_map", {})
            cls._loaded = True
            logger.info("classifier keywords loaded from YAML")
        except Exception as e:
            logger.warning(f"YAML load failed ({e}), using defaults")
            cls._load_defaults()
            cls._loaded = True

    @classmethod
    def _load_defaults(cls):
        cls._keywords = {
            "analysis": ["분석", "analyze", "데이터 분석", "리포트"],
            "analysis_result": ["분석 결과", "분석결과", "리포트 보여"],
            "math_model": ["수학 모델", "수학모델", "모델링", "modeling", "수식", "변수 정의", "제약 정의"],
            "pre_decision": ["솔버", "solver", "추천", "recommend", "시뮬레이션", "정확도 우선", "속도 우선", "비용 우선"],
            "execution": ["실행", "execute", "run", "최적화 실행"],
            "show_result": ["결과", "result", "결과 보여"],
            "show_solver": ["솔버 결과", "추천 결과"],
            "show_opt_result": ["최적화 결과", "최종 결과"],
            "reset": ["리셋", "reset", "초기화", "처음부터"],
            "guide": ["도움", "help", "가이드", "뭐해", "다음 단계"],
            "domain_change": ["도메인 변경", "도메인 수정"],
        }
        cls._domain_map = {
            "항공": "aviation", "철도": "railway", "버스": "bus",
            "물류": "logistics", "병원": "hospital",
        }

    @classmethod
    def quick_classify(cls, message: str, has_file: bool = False, current_tab: Optional[str] = None) -> Optional[str]:
        """
        키워드 매칭으로 빠르게 분류. 확실한 경우만 반환.
        애매하면 None → LLM에게 위임.
        """
        cls._load_keywords()

        if has_file and not message.strip():
            return "FILE_UPLOAD"

        msg = message.lower().strip()

        # ★ 질문 패턴 감지: 질문어미가 있으면 LLM에게 넘김
        question_endings = [
            "인가요?", "인가요", "뭔가요?", "뭔가요", "건가요?", "건가요",
            "나요?", "나요", "할까요?", "할까요", "을까요?", "을까요",
            "는지요?", "는지요", "는건지", "어떤가요?", "어떤가요",
            "어떻게", "왜", "무엇", "뭐가", "뭘",
            "알려주세요", "알려줘", "설명해주세요", "설명해줘",
            "파악되나요", "되나요", "있나요", "없나요",
        ]
        if any(msg.endswith(q) or q in msg for q in question_endings):
            # 단, 명시적 실행 요청("~해줘", "~시작")이 함께 있으면 키워드 매칭 진행
            action_keywords = ["해줘", "시작", "실행", "생성해", "확정", "추천해"]
            if not any(ak in msg for ak in action_keywords):
                return None

        # 특수 명령 (항상 키워드로 처리)
        for intent in ["reset", "guide", "domain_change"]:
            if any(kw in msg for kw in cls._keywords.get(intent, [])):
                return intent.upper()

        # 파일 + 명령 동시 → 명령 우선
        if has_file:
            for intent in ["analysis", "execution", "pre_decision"]:
                if any(kw in msg for kw in cls._keywords.get(intent, [])):
                    return intent.upper() if intent != "analysis" else "ANALYZE"
            return "FILE_UPLOAD"

        # 명확한 키워드 매칭
        if any(kw in msg for kw in cls._keywords.get("execution", [])):
            return "START_OPTIMIZATION"
        if any(kw in msg for kw in cls._keywords.get("show_opt_result", [])):
            return "SHOW_OPT_RESULT"
        if any(kw in msg for kw in cls._keywords.get("show_solver", [])):
            return "SHOW_SOLVER"
        if any(kw in msg for kw in cls._keywords.get("show_math_model", [])):
            return "SHOW_MATH_MODEL"
        if any(kw in msg for kw in cls._keywords.get("math_model", [])):
            return "MATH_MODEL"
        if any(kw in msg for kw in cls._keywords.get("show_result", [])):
            return "SHOW_RESULT"
        if any(kw in msg for kw in cls._keywords.get("pre_decision", [])):
            return "PRE_DECISION"
        if any(kw in msg for kw in cls._keywords.get("analysis_result", [])):
            return "SHOW_ANALYSIS"
        if any(kw in msg for kw in cls._keywords.get("analysis", [])):
            return "ANALYZE"

        # ── Tab-context aware classification ──
        # 1순위: 메시지에 명시적 대상 + 실행 동사
        tab_keyword_map = {
            "analysis": ["분석", "데이터 분석", "리포트", "analyze"],
            "math_model": ["수학 모델", "수학모델", "모델링", "목적함수", "제약조건", "변수", "수식"],
            "solver": ["솔버", "solver", "추천", "컴파일"],
            "result": ["결과", "실행", "최적화"],
        }
        intent_from_tab = {
            "analysis": "ANALYZE",
            "math_model": "MATH_MODEL",
            "solver": "PRE_DECISION",
            "result": "START_OPTIMIZATION",
        }
        action_verbs = ["해줘", "해주세요", "시작", "실행", "생성", "바꿔", "변경", "수정", "다시", "재생성"]

        for tab_key, keywords in tab_keyword_map.items():
            if any(kw in msg for kw in keywords):
                if any(v in msg for v in action_verbs):
                    resolved = intent_from_tab.get(tab_key)
                    if resolved:
                        logger.info(f"Keyword+verb resolved: {tab_key} -> {resolved}")
                        return resolved
                break  # Found target but no action verb -> let LLM handle

        # 2순위: 모호한 메시지 + current_tab 컨텍스트
        if current_tab:
            if any(v in msg for v in action_verbs):
                resolved = intent_from_tab.get(current_tab)
                if resolved:
                    logger.info(f"Tab-context resolved: tab={current_tab} -> {resolved}")
                    return resolved

        # 매칭 안 됨 → LLM에게 위임
        return None

    @classmethod
    def extract_domain_from_message(cls, message: str) -> Optional[str]:
        cls._load_keywords()
        msg = message.lower()
        for keyword, domain in cls._domain_map.items():
            if keyword in msg:
                return domain
        return None


# ============================================================
# 4. LLM 응답 파서 (Skill JSON 추출)
# ============================================================

# Skill명 → 내부 intent 매핑
SKILL_TO_INTENT = {
    "FileReceivedSkill": "FILE_UPLOAD",
    "AnalyzeDataSkill": "ANALYZE",
    "PreDecisionSkill": "PRE_DECISION",
    "MathModelSkill": "MATH_MODEL",
    "StartOptimizationSkill": "START_OPTIMIZATION",
    "ShowResultSkill": "SHOW_OPT_RESULT",
    "AnswerQuestionSkill": "ANSWER",
    "GeneralReplySkill": "GENERAL",
    "UpdateWorkspaceSkill": "UPDATE_WORKSPACE",
    "AskForDataSkill": "ASK_FOR_DATA",
}


def parse_skill_from_llm(response_text: str) -> tuple[Optional[str], Dict[str, Any]]:
    """
    LLM 응답에서 Skill JSON을 추출.
    반환: (intent, parameters) 또는 (None, {}) if 파싱 실패
    """
    text = response_text.strip()

    # 마크다운 코드블록 제거
    code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_match:
        text = code_match.group(1)

    # JSON 추출 시도
    json_str = None

    # 1) 중괄호로 시작하는 JSON 찾기
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        json_str = brace_match.group(0)

    if not json_str:
        return None, {}

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        return None, {}

    # skill / tool_code / tool_name 중 하나에서 스킬명 추출
    skill_name = (
        parsed.get("skill")
        or parsed.get("tool_code")
        or parsed.get("tool_name")
        or ""
    )
    parameters = parsed.get("parameters", {})

    intent = SKILL_TO_INTENT.get(skill_name)
    if intent:
        return intent, parameters

    # 부분 매칭
    for known_skill, mapped_intent in SKILL_TO_INTENT.items():
        if known_skill.lower() in skill_name.lower():
            return mapped_intent, parameters

    return None, {}


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
        path = Path(__file__).parents[2] / "Prompts" / "crew" / "system.md"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("system.md not found")
            return "You are a crew scheduling optimization assistant."

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
            # ── 이벤트 기반 (파일 업로드) ──
            if event_type == "file_upload":
                return await self._handle_file_upload(session, project_id, event_data)

            # ── 1차: 키워드 빠른 우선분류 ──
            quick_intent = InputClassifier.quick_classify(message, has_file=has_file, current_tab=current_tab)

            if quick_intent:
                logger.info(f"[{project_id}] quick_intent={quick_intent}")
                session.history.append({"role": "user", "content": message})

                direct_handlers = {
                    "RESET": self._handle_reset,
                    "GUIDE": self._handle_guide,
                    "DOMAIN_CHANGE": self._handle_domain_change,
                    "FILE_UPLOAD": self._handle_file_upload,
                }

                if quick_intent in direct_handlers:
                    if quick_intent == "FILE_UPLOAD":
                        return await direct_handlers[quick_intent](session, project_id, event_data)
                    return await direct_handlers[quick_intent](session, project_id, message)

                if session.state.math_model and not session.state.math_model_confirmed:
                    confirm_keywords = ["확정", "확인", "맞", "다시", "재생성", "목적함수", "변경"]
                    if any(kw in message for kw in confirm_keywords):
                        return await self._handle_math_model_confirm(session, project_id, message)

                return await self._execute_skill(session, project_id, quick_intent, message, {})

            # ── 2차: LLM 스킬 선택 ──
            logger.info(f"[{project_id}] → LLM skill selection")
            session.history.append({"role": "user", "content": message})
            return await self._llm_select_and_execute(session, project_id, message, current_tab)

    # ----------------------------------------------------------
    # LLM Skill 선택 → 파싱 → 실행
    # ----------------------------------------------------------
    async def _llm_select_and_execute(
        self, session: CrewSession, project_id: str, message: str, current_tab: Optional[str] = None
    ) -> Dict:
        if not self.model:
            return self._error_response("AI 모델에 연결할 수 없습니다.")

        try:
            state = session.state

            # 작업 이력 요약 생성
            action_history = self._build_action_history(session)

            prompt = (
                f"{state.to_state_block()}\n"
                f"{action_history}\n"
                f"[USER MESSAGE]\n{message}\n\n"
                f"[CURRENT TAB] {current_tab or 'none'}\n"
                f"(사용자가 현재 보고 있는 화면. 모호한 요청 해석 시 참고)\n\n"
                f"[INSTRUCTIONS]\n"
                f"위 상태와 사용자 메시지를 분석하여, 아래 Skill 중 하나를 선택하고 반드시 JSON으로만 응답하세요.\n\n"
                f"사용 가능한 Skill 목록:\n"
                f"- AnalyzeDataSkill: 데이터 분석 (재분석 포함)\n"
                f"- MathModelSkill: 수학 모델 생성/재생성/수정\n"
                f"- PreDecisionSkill: 솔버 추천/시뮬레이션\n"
                f"- StartOptimizationSkill: 최적화 실행/재실행\n"
                f"- ShowResultSkill: 이전 결과 재확인\n"
                f"- AnswerQuestionSkill: 질문 답변 (데이터, 모델, 결과, 도메인 지식 등)\n"
                f"- GeneralReplySkill: 일반 대화/인사/기타\n\n"
                f"[파라미터 추출 규칙]\n"
                f"사용자 메시지에서 핵심 정보를 구조화된 파라미터로 추출하세요:\n"
                f"- MathModelSkill: user_objective(목적함수 변경 시), modify_constraints(제약조건 수정 시), regenerate(true/false)\n"
                f"- StartOptimizationSkill: solver_preference(선호 솔버), rerun(재실행 여부)\n"
                f"- AnswerQuestionSkill: query(질문 원문), about(질문 대상: model/result/data/domain/general)\n"
                f"- AnalyzeDataSkill: reanalyze(재분석 여부), focus(특정 관점)\n\n"
                f"[스킬 선택 기준]\n"
                f"- 명확한 실행 요청(~해줘, ~시작, ~바꿔줘) -> 해당 Action 스킬\n"
                f"- 질문형(~인가요?, 왜~?, ~알려줘) -> AnswerQuestionSkill\n"
                f"- 모호한 경우: 질문이면 AnswerQuestionSkill, 실행이면 해당 Action 스킬\n\n"
                f"응답 형식 (JSON만 출력, 다른 텍스트 금지):\n"
                f'{{"skill": "스킬명", "parameters": {{"key": "value"}}}}\n\n'
                f"예시:\n"
                f'- 사용자가 질문하면: {{"skill": "AnswerQuestionSkill", "parameters": {{"query": "변수수는 어떻게 나오나요?"}}}}\n'
                f'- 분석 요청: {{"skill": "AnalyzeDataSkill", "parameters": {{}}}}\n'
                f'- 수학 모델 요청: {{"skill": "MathModelSkill", "parameters": {{}}}}\n'
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
                return await self._execute_skill(session, project_id, intent, message, parameters)

            # JSON 파싱 실패 → 텍스트에서 스킬명 감지 시도
            skill_name_map = {
                "AnswerQuestionSkill": "ANSWER",
                "GeneralReplySkill": "GENERAL",
                "AnalyzeDataSkill": "ANALYZE",
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
            cleaned = self._clean_report(llm_text)
            if cleaned and len(cleaned) > 20 and not cleaned.startswith("{"):
                return {
                    "type": "text",
                    "text": cleaned,
                    "data": None,
                    "options": self._build_next_options(session.state)
                }

            # 그 외 모든 경우 → _skill_answer로 직접 답변 생성
            logger.info(f"[{project_id}] Fallback to _skill_answer")
            return await self._skill_answer(session, project_id, message, {})

        except Exception as e:
            logger.error(f"LLM skill selection failed: {e}", exc_info=True)
            return self._error_response("요청 처리 중 오류가 발생했습니다.")

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
            "ANALYZE": self._skill_analyze,
            "SHOW_ANALYSIS": self._skill_show_analysis,
            "PRE_DECISION": self._skill_pre_decision,
            "SHOW_MATH_MODEL": self._skill_show_math_model,
            "MATH_MODEL": self._skill_math_model,
            "START_OPTIMIZATION": self._skill_start_optimization,
            "SHOW_RESULT": self._skill_show_analysis,
            "SHOW_SOLVER": self._skill_show_solver,
            "SHOW_OPT_RESULT": self._skill_show_opt_result,
            "ANSWER": self._skill_answer,
            "GENERAL": self._skill_general,
            "UPDATE_WORKSPACE": self._skill_general,
            "ASK_FOR_DATA": self._skill_ask_for_data,
        }

        handler = handlers.get(intent, self._skill_general)
        result = await handler(session, project_id, message, parameters)

        # Action intent 처리 후 target_tab 추가 (프론트엔드 자동 탭 전환용)
        intent_to_tab = {
            "ANALYZE": "analysis",
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
    async def _handle_file_upload(
        self, session: CrewSession, project_id: str, event_data: Optional[Dict]
    ) -> Dict:
        state = session.state
        files = []

        if event_data and "files" in event_data:
            files = event_data["files"]
            state.uploaded_files = [f.get("filename", "unknown") for f in files]
        elif event_data and "uploaded_files" in event_data:
            files = event_data["uploaded_files"]
            state.uploaded_files = [f.get("filename", "unknown") for f in files]

        state.file_uploaded = True
        state.last_executed_skill = "FileReceivedSkill"

        # Save dataset version
        try:
            pid = int(project_id)
            domain = state.domain_override or state.detected_domain
            dv = create_dataset_version(
                project_id=pid,
                file_list=state.uploaded_files,
                domain_type=domain,
                description="파일 업로드",
            )
            state.current_dataset_version_id = dv.id
        except Exception as ve:
            logger.warning(f"Failed to create dataset version: {ve}")

        file_list_text = "\n".join([f"  • `{name}`" for name in state.uploaded_files])
        file_count = len(state.uploaded_files)

        return {
            "type": "file_upload",
            "text": (
                f"📁 **파일 업로드가 완료되었습니다.**\n\n"
                f"업로드된 파일 ({file_count}개):\n{file_list_text}\n\n"
                f"다음 단계를 선택해 주세요."
            ),
            "data": {
                "view_mode": "file_uploaded",
                "files": state.uploaded_files,
                "file_count": file_count,
            },
            "options": [
                {"label": "📊 데이터 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"},
                {"label": "📖 사용 가이드", "action": "send", "message": "가이드 보여줘"},
            ],
        }

    # ----------------------------------------------------------
    # Skill: AnalyzeData
    # ----------------------------------------------------------
    async def _skill_analyze(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state

        if not state.file_uploaded:
            return {
                "type": "warning",
                "text": "⚠️ 파일이 업로드되지 않았습니다. 먼저 스케줄 데이터 파일을 업로드해 주세요.",
                "data": None,
                "options": [{"label": "📁 파일 업로드", "action": "upload"}],
            }


        # 이미 분석 완료된 경우 캐시 반환 (변경 요청이 아니면)
        if state.analysis_completed and state.last_analysis_report:
            if any(kw in message for kw in ["다시", "재분석", "재 분석", "reanalyze"]):
                state.reset_from_analysis()
                save_session_state(project_id, state)
                # 아래 분석 로직으로 계속 진행
            else:
                domain = state.domain_override or state.detected_domain
                confidence = 1.0 if state.domain_override else state.domain_confidence
                display = self._domain_display(domain)
                confidence_pct = int(confidence * 100)
                return {
                    "type": "analysis",
                    "text": (
                        f" **이전 분석 결과입니다.**\n\n"
                        f"감지된 도메인: {display}\n"
                        f"확신도: {confidence_pct}%\n\n"
                        f"오른쪽 패널에서 상세 리포트를 확인해 주세요."
                    ),
                    "data": {
                        "view_mode": "report",
                        "report": state.last_analysis_report,
                        "agent_status": "분석 완료",
                        "domain": domain,
                        "domain_confidence": confidence_pct,
                        "actions": {
                            "primary": {"label": " 수학 모델 생성", "message": "수학 모델 생성해줘"},
                            "secondary": {"label": " 다시 분석", "message": "다시 분석해줘"},
                        },
                    },
                    "options": [
                        {"label": " 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
                        {"label": " 다시 분석", "action": "send", "message": "다시 분석해줘"},
                        {"label": " 도메인 변경", "action": "send", "message": "도메인 변경"},
                    ],
                }

        try:
            csv_summary = await analyze_csv_summary(project_id)
            session.state.csv_summary = csv_summary

            # ★ 팩트 데이터 추출 (코드로 계산된 정확한 값)
            from engine.file_service import extract_data_facts_async
            data_facts = await extract_data_facts_async(project_id)
            state.data_facts = data_facts

            # 도메인 감지
            if not state.domain_override:
                detected = InputClassifier.extract_domain_from_message(message)
                if not detected and csv_summary:
                    detected = InputClassifier.extract_domain_from_message(csv_summary)
                if detected:
                    state.detected_domain = detected
                    state.domain_confidence = 0.75
                if not state.detected_domain:
                    state.detected_domain = "general"
                    state.domain_confidence = 0.3

            domain = state.domain_override or state.detected_domain
            confidence = 1.0 if state.domain_override else state.domain_confidence

            # 팩트 요약을 프롬프트에 포함
            facts_summary = self._build_facts_summary(data_facts)

            prompt = build_analysis_prompt(
                csv_summary=csv_summary or "데이터 요약을 생성할 수 없습니다.",
                context=state.context_string(),
                detected_domain=domain,
                domain_confidence=confidence,
                data_facts=facts_summary,
            )
            response = await asyncio.to_thread(
                self.model.generate_content, prompt
            )
            report = response.text.strip()
            report = self._clean_report(report)

            state.analysis_completed = True
            state.last_analysis_report = report
            state.last_executed_skill = "AnalyzeDataSkill"

            display = self._domain_display(domain)
            confidence_pct = int(confidence * 100)

            return {
                "type": "analysis",
                "text": (
                    f"📊 **데이터 분석이 완료되었습니다.**\n\n"
                    f"감지된 도메인: {display}\n"
                    f"확신도: {confidence_pct}%\n\n"
                    f"오른쪽 패널에서 상세 리포트를 확인해 주세요."
                ),
                "data": {
                    "view_mode": "report",
                    "report": report,
                    "agent_status": "분석 완료",
                    "domain": domain,
                    "domain_confidence": confidence_pct,
                    "actions": {
                        "primary": {"label": "📐 수학 모델 생성", "message": "수학 모델 생성해줘"},
                        "secondary": {"label": "🔄 다시 분석", "message": "다시 분석해줘"},
                    },
                },
                "options": [
                    {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
                    {"label": "🔄 다시 분석", "action": "send", "message": "다시 분석해줘"},
                    {"label": "🌐 도메인 변경", "action": "send", "message": "도메인 변경"},
                ],
            }

        except Exception as e:
            logger.error(f"AnalyzeDataSkill failed: {e}", exc_info=True)
            return self._error_response("분석 중 오류가 발생했습니다.", "분석 시작해줘")

    # ----------------------------------------------------------
    # Skill: ShowMathModel
    # ----------------------------------------------------------

    async def _skill_show_math_model(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state
        if state.math_model:
            summary = summarize_model(state.math_model)
            if state.math_model_confirmed:
                return {
                    "type": "analysis",
                    "text": "📐 **확정된 수학 모델입니다.**",
                    "data": {
                        "view_mode": "math_model",
                        "math_model": state.math_model,
                        "math_model_summary": summary,
                    },
                    "options": [
                        {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                        {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                        {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                    ],
                }
            else:
                return {
                    "type": "analysis",
                    "text": "📐 **이전에 생성된 수학 모델입니다.**\n\n확인 후 다음 단계를 진행해 주세요.",
                    "data": {
                        "view_mode": "math_model",
                        "math_model": state.math_model,
                        "math_model_summary": summary,
                    },
                    "options": [
                        {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                        {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                        {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                    ],
                }
        return {
            "type": "warning",
            "text": "아직 생성된 수학 모델이 없습니다. 먼저 수학 모델을 생성해 주세요.",
            "data": None,
            "options": [
                {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
            ],
        }
    
    # ----------------------------------------------------------
    # Skill: ShowAnalysis (캐시 결과)
    # ----------------------------------------------------------
    async def _skill_show_analysis(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state
        if state.last_analysis_report:
            return {
                "type": "analysis",
                "text": "📊 **이전 분석 결과입니다.**\n\n오른쪽 패널에서 확인해 주세요.",
                "data": {
                    "report": state.last_analysis_report,
                    "agent_status": "분석 완료 (캐시)",
                    "actions": {
                        "primary": {"label": "📐 수학 모델 생성", "message": "수학 모델 생성해줘"},
                        "secondary": {"label": "🔄 다시 분석", "message": "다시 분석해줘"},
                    },
                },
                "options": [
                    {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
                    {"label": "🔄 다시 분석", "action": "send", "message": "다시 분석해줘"},
                ],
            }
        return {
            "type": "warning",
            "text": "⚠️ 아직 분석 결과가 없습니다. 먼저 데이터 분석을 진행해 주세요.",
            "data": None,
            "options": [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}],
        }

    # ----------------------------------------------------------
    # Skill: PreDecision (솔버 추천)
    # ----------------------------------------------------------
    async def _skill_pre_decision(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state

        if not state.analysis_completed:
            return {
                "type": "warning",
                "text": "먼저 데이터 분석을 완료해 주세요.",
                "data": None,
                "options": [{"label": "데이터 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}],
            }

        if not state.math_model:
            return {
                "type": "warning",
                "text": "수학 모델이 아직 생성되지 않았습니다. 먼저 수학 모델을 생성해 주세요.",
                "data": None,
                "options": [{"label": "수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"}],
            }

        # ★ 변경 1: 메시지에서 priority 파싱
        priority = "auto"
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ["정확도 우선", "accuracy", "정확도"]):
            priority = "accuracy"
        elif any(kw in msg_lower for kw in ["속도 우선", "speed", "속도", "빠른"]):
            priority = "speed"
        elif any(kw in msg_lower for kw in ["비용 우선", "cost", "비용", "저렴"]):
            priority = "cost"

        # ★ 변경 2: priority가 auto가 아니면(사용자가 우선순위 변경 버튼 클릭) 캐시 무시
        if state.last_pre_decision_result and priority == "auto":
            return self._build_solver_response(state, state.last_pre_decision_result)

        try:
            result = await run_pre_decision_analysis(
                math_model=state.math_model,
                priority=priority,          # ★ 변경 3: 파싱된 priority 전달
                data_facts=state.data_facts,
                project_id=project_id,
            )
            state.last_pre_decision_result = result
            state.pre_decision_done = True
            state.last_executed_skill = "PreDecisionSkill"

            solvers = result.get("recommended_solvers", [])
            if solvers:
                top = solvers[0]
                state.solver_selected = f"{top.get('provider', '')} {top.get('solver_name', '')}".strip()
            else:
                state.solver_selected = "Classical CPU"

            return self._build_solver_response(state, result)

        except Exception as e:
            logger.error(f"[{project_id}] Pre-decision error: {e}", exc_info=True)
            return {
                "type": "error",
                "text": f"솔버 추천 중 오류가 발생했습니다: {str(e)}",
                "data": None,
                "options": [],
            }

    def _build_solver_response(self, state: SessionState, result: Dict) -> Dict:
        solvers = result.get("recommended_solvers", [])
        profile = result.get("problem_profile", {})
        summary = result.get("summary", "")
        top = result.get("top_recommendation")
        solver_name = state.solver_selected or "미정"

        # 우선순위 변경 옵션
        priority_options = [
            {"label": "🚀 최적화 실행", "action": "send", "message": f"{solver_name}으로 최적화 실행해줘"},
            {"label": "🎯 정확도 우선", "action": "send", "message": "정확도 우선으로 솔버 추천해줘"},
            {"label": "⚡ 속도 우선", "action": "send", "message": "속도 우선으로 솔버 추천해줘"},
            {"label": "💰 비용 우선", "action": "send", "message": "비용 우선으로 솔버 추천해줘"},
            {"label": "🔄 자동 추천", "action": "send", "message": "솔버 다시 추천해줘"},
        ]

        # 텍스트 구성
        text_parts = [            f"⚙️ **솔버 추천이 완료되었습니다.**\n",
            f"📊 **문제 프로파일**",
            f"- 변수 수: {profile.get('variable_count', 0):,}개",
            f"- 제약조건: {profile.get('constraint_count', 0)}개",
            f"- 변수 타입: {', '.join(profile.get('variable_types', []))}",
            f"- 문제 유형: {', '.join(profile.get('problem_classes', []))}",
            "",
            f"🏆 **최적 추천: {solver_name}**",
        ]

        if top:
            text_parts.append(f"- 적합도: {top.get('suitability', '')} (점수: {top.get('total_score', 0)})")
            text_parts.append(f"- {top.get('description', '')}")
            if top.get("reasons"):
                for r in top["reasons"]:
                    text_parts.append(f"  ✅ {r}")
            if top.get("warnings"):
                for w in top["warnings"]:
                    text_parts.append(f"  ⚠️ {w}")

        text_parts.append("\n오른쪽 패널에서 상세 정보를 확인해주세요.")

        return {
            "type": "analysis",
            "text": "\n".join(text_parts),
            "data": {
                "view_mode": "solver",
                "problem_profile": profile,
                "recommended_solvers": solvers,
                "top_recommendation": top,
                "priority": result.get("priority", "auto"),
                "execution_strategies": result.get("execution_strategies", []),
                "recommended_strategy": result.get("recommended_strategy"),
                "model_analysis": result.get("model_analysis")
            },
            "options": priority_options,
        }
    
    # ----------------------------------------------------------
    # Skill: MathModel (수학 모델 생성)
    # ----------------------------------------------------------
    async def _skill_math_model(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state

        if not state.analysis_completed:
            return {
                "type": "warning",
                "text": "⚠️ 아직 데이터 분석이 완료되지 않았습니다. 먼저 '데이터 분석'을 진행해 주세요.",
                "data": None,
                "options": [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}],
            }

        if state.math_model and not state.math_model_confirmed:
            summary = summarize_model(state.math_model)
            return {
                "type": "analysis",
                "text": "📐 **이전에 생성된 수학 모델입니다.**\n\n확인 후 다음 단계를 진행해 주세요.",
                "data": {
                    "view_mode": "math_model",
                    "math_model": state.math_model,
                    "math_model_summary": summary,
                },
                "options": [
                    {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "✏️ 목적함수 변경", "action": "send", "message": "목적함수를 비용 최소화로 변경해줘"},
                    {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                ],
            }

        # 이미 확정된 모델이 있는 경우
        if state.math_model and state.math_model_confirmed:
            # 재생성/수정 요청이면 초기화 후 아래 생성 로직으로
            is_regenerate = any(kw in message for kw in ["다시", "재생성", "regenerate", "바꿔", "변경", "수정"])
            is_param_action = params and params.get("user_objective")
            is_param_regen = params and params.get("regenerate")
            if is_regenerate or is_param_action or is_param_regen:
                state.reset_from_math_model()
                save_session_state(project_id, state)
            else:
                summary = summarize_model(state.math_model)
                return {
                    "type": "analysis",
                    "text": "📐 **이미 확정된 수학 모델입니다.**\n\n재생성하려면 '모델 재생성'을 눌러주세요.",
                    "data": {
                        "view_mode": "math_model",
                        "math_model": state.math_model,
                        "math_model_summary": summary,
                    },
                    "options": [
                        {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                        {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                        {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                    ],
                }

        try:
            # 사용자가 목적함수를 지정했는지 확인
            # 1순위: LLM이 추출한 구조화된 파라미터
            user_objective = params.get("user_objective") if params else None

            # 2순위: 메시지에서 키워드 기반 추출 (fallback)
            if not user_objective:
                objective_keywords = ["최소화", "최대화", "minimize", "maximize", "공평", "비용", "균등", "운행시간", "인건비"]
                if any(kw in message for kw in objective_keywords):
                    user_objective = message

            csv_summary = state.csv_summary or ""
            analysis_report = state.last_analysis_report or ""
            domain = state.detected_domain or "generic"

            result = await generate_math_model(
                csv_summary=csv_summary,
                analysis_report=analysis_report,
                domain=domain,
                user_objective=user_objective,
                data_facts=state.data_facts,
            )

            if not result["success"]:
                error_msg = result.get("error", "알 수 없는 오류")
                warnings = result.get("validation", {}).get("warnings", []) if result.get("validation") else []
                warning_text = "\n".join([f"  ⚠️ {w}" for w in warnings]) if warnings else ""
                return self._error_response(
                    f"수학 모델 생성에 실패했습니다: {error_msg}\n{warning_text}",
                    "수학 모델 생성해줘"
                )

            model = result["model"]
            validation = result["validation"]

            # 세션에 저장
            state.math_model = model
            state.math_model_confirmed = False
            state.last_executed_skill = "MathModelSkill"

            # 사용자에게 보여줄 요약
            summary = summarize_model(model)

            # 검증 경고 표시
            warning_lines = []
            if validation.get("warnings"):
                warning_lines.append("\n### ⚠️ 검증 경고")
                for w in validation["warnings"]:
                    warning_lines.append(f"- {w}")

            meta = model.get("metadata", {})
            var_count = meta.get("estimated_variable_count", "?")
            con_count = meta.get("estimated_constraint_count", "?")

            return {
                "type": "analysis",
                "text": (
                    f"📐 **수학 모델이 생성되었습니다.**\n\n"
                    f"추정 변수 수: **{var_count}개**\n"
                    f"추정 제약 수: **{con_count}개**\n\n"
                    f"오른쪽 패널에서 상세 모델을 확인하고, 맞으면 '모델 확정'을 눌러주세요."
                ),
                "data": {
                    "view_mode": "math_model",
                    "math_model": model,
                    "math_model_summary": summary + "\n".join(warning_lines)
                },
                "options": [
                    {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "✏️ 목적함수 변경", "action": "send", "message": "목적함수를 변경하고 싶어요"},
                    {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                ],
            }

        except Exception as e:
            logger.error(f"MathModelSkill failed: {e}", exc_info=True)
            return self._error_response("수학 모델 생성 중 오류가 발생했습니다.", "수학 모델 생성해줘")

    # ----------------------------------------------------------
    # Skill: MathModel 확정/재생성 처리
    # ----------------------------------------------------------
    async def _handle_math_model_confirm(
        self, session: CrewSession, project_id: str, message: str, current_tab: Optional[str] = None
    ) -> Dict:
        state = session.state
        msg = message.lower()

        # 모델 확정
        if "확정" in msg or "확인" in msg or "맞" in msg:
            state.math_model_confirmed = True
            meta = state.math_model.get("metadata", {}) if state.math_model else {}

            # Save model version
            try:
                pid = int(project_id)
                meta = state.math_model.get("metadata", {}) if state.math_model else {}
                domain = state.domain_override or state.detected_domain
                obj_func = ""
                if state.math_model and "formulation" in state.math_model:
                    obj_func = state.math_model["formulation"].get("objective", {}).get("description", "")
                mv = create_model_version(
                    project_id=pid,
                    dataset_version_id=getattr(state, "current_dataset_version_id", None),
                    model_json=state.math_model,
                    domain_type=domain,
                    objective_type=meta.get("problem_type", "unknown"),
                    objective_summary=obj_func[:200] if obj_func else None,
                    variable_count=meta.get("estimated_variable_count"),
                    constraint_count=meta.get("estimated_constraint_count"),
                    description="모델 확정",
                )
                state.current_model_version_id = mv.id
            except Exception as ve:
                logger.warning(f"Failed to create model version: {ve}")
            var_count = meta.get("estimated_variable_count", "?")
            return {
                "type": "system",
                "text": (
                    f"✅ **수학 모델이 확정되었습니다.**\n\n"
                    f"변수 규모({var_count}개)를 기반으로 솔버를 추천합니다."
                ),
                "data": None,
                "options": [
                    {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                ],
            }

        # 재생성 요청
        if "다시" in msg or "재생성" in msg:
            state.reset_from_math_model()
            return await self._skill_math_model(session, project_id, message, {})

        # 목적함수 변경 요청
        if "목적" in msg or "변경" in msg:
            state.reset_from_math_model()
            return await self._skill_math_model(session, project_id, message, {})

        # 기타 → 일반 처리
        return await self._skill_general(session, project_id, message, {})

    # ----------------------------------------------------------
    # Skill: StartOptimization
    # ----------------------------------------------------------
    async def _skill_start_optimization(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state

        if not state.pre_decision_done:
            return {
                "type": "warning",
                "text": " 최적화 실행 전에 먼저 솔버 추천(시뮬레이션)을 진행해 주세요.",
                "data": None,
                "options": [{"label": " 솔버 추천", "action": "send", "message": "솔버 추천해줘"}],
            }

        solver_name = params.get('selected_solver') or state.solver_selected or 'Unknown'

        # 세션에 실제 실행 결과가 있으면 그것을 사용
        if state.last_optimization_result:
            real_result = state.last_optimization_result
            state.optimization_done = True
            return {
                "type": "analysis",
                "text": (
                    f" **최적화가 완료되었습니다!**\n\n"
                    f"사용 솔버: **{solver_name}**\n"
                    f"상태: {real_result.get('status', 'UNKNOWN')}\n"
                    f"목적함수 값: {real_result.get('objective_value', '-')}\n\n"
                    f"오른쪽 패널에서 상세 결과를 확인해 주세요."
                ),
                "data": {
                    "view_mode": "result",
                    "result": real_result,
                    "solver": solver_name,
                },
                "options": [
                    {"label": " 리포트 다운로드", "action": "download"},
                    {"label": " 다시 실행", "action": "send", "message": "최적화 다시 실행해줘"},
                ],
            }

        # 실행 결과가 없으면 프론트에서 /api/solve로 직접 실행하도록 안내
        return {
            "type": "info",
            "text": (
                f" 오른쪽 패널의 솔버 화면에서 실행 버튼을 눌러주세요.\n\n"
                f"선택된 솔버: **{solver_name}**"
            ),
            "data": None,
            "options": [
                {"label": " 솔버 추천 보기", "action": "send", "message": "솔버 추천 결과 보여줘"},
            ],
        }

    # ----------------------------------------------------------
    # Skill: ShowResult / ShowSolver / ShowOptResult
    # ----------------------------------------------------------
    async def _skill_show_solver(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state
        if state.last_pre_decision_result:
            return self._build_solver_response(state, state.last_pre_decision_result)
        return {
            "type": "warning",
            "text": "⚠️ 솔버 추천 결과가 없습니다.",
            "data": None,
            "options": [{"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"}],
        }

    async def _skill_show_opt_result(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        state = session.state
        if state.last_optimization_result:
            return {
                "type": "analysis",
                "text": "📈 **이전 최적화 결과입니다.**",
                "data": {"view_mode": "result", "result": state.last_optimization_result},
                "options": [
                    {"label": "📥 다운로드", "action": "download"},
                    {"label": "🔄 다시 실행", "action": "send", "message": "최적화 다시 실행해줘"},
                ],
            }
        return {
            "type": "warning",
            "text": "⚠️ 아직 최적화가 실행되지 않아 표시할 결과가 없습니다.",
            "data": None,
            "options": [{"label": "🚀 최적화 실행", "action": "send", "message": "최적화 실행해줘"}],
        }

    # ----------------------------------------------------------
    # Skill: AnswerQuestion
    # ----------------------------------------------------------
    async def _skill_answer(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        # LLM이 직접 답변을 제공한 경우 (answer 또는 message 키)
        answer = params.get("answer", "") or params.get("message", "")
        if answer and not answer.startswith("{"):
            return {
                "type": "text",
                "text": self._clean_report(answer),
                "data": None,
                "options": self._build_next_options(session.state)
            }

        # 답변이 없으면 데이터를 포함하여 LLM에게 직접 질문
        state = session.state
        query = params.get("query", "") or message

        if self.model:
            try:
                context_parts = [
                    "당신은 KQC 최적화 에이전트입니다. 사용자의 질문에 대해 직접 답변하세요.",
                    "중요: 반드시 질문에 대한 답변 텍스트만 출력하세요.",
                    "절대 스킬명(AnalyzeDataSkill 등)이나 JSON을 출력하지 마세요.",
                    "절대 '~를 실행하겠습니다', '~를 수행합니다' 같은 안내를 하지 마세요.",
                    "데이터나 분석 관련 질문이면 아래 데이터를 근거로 설명하고,",
                    "일반적인 질문이면 친절하게 답변하세요.",
                    "",
                    f"[현재 상태] {state.context_string()}",
                    "",
                    self._build_action_history(session),
                ]

                if state.csv_summary:
                    context_parts.append("")
                    context_parts.append("[업로드된 데이터 요약]")
                    context_parts.append(state.csv_summary[:3000])

                if state.last_analysis_report:
                    context_parts.append("")
                    context_parts.append("[분석 리포트]")
                    context_parts.append(state.last_analysis_report[:2000])

                context_parts.append("")
                context_parts.append(f"[사용자 질문] {query}")

                context = "\n".join(context_parts)

                response = await asyncio.to_thread(
                    self.model.generate_content, context
                )
                reply_text = response.text.strip()
                reply_text = self._extract_text_from_llm(reply_text)
                return {
                    "type": "text",
                    "text": reply_text,
                    "data": None,
                    "options": self._build_next_options(state)
                }
            except Exception as e:
                logger.error(f"AnswerQuestion LLM error: {e}")

        return await self._skill_general(session, project_id, message, params)

    # ----------------------------------------------------------
    # Skill: GeneralReply / AskForData
    # ----------------------------------------------------------
    async def _skill_general(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        # params에 message가 있으면 그대로 사용
        reply = params.get("message", "")
        if reply:
            return {
                "type": "text",
                "text": self._clean_report(reply),
                "data": None,
                "options": self._build_next_options(session.state)
            }

         # LLM fallback
        if self.model:
            try:
                state = session.state

                # ── 풍부한 컨텍스트 구성 ──
                context_parts = [
                    "당신은 KQC 최적화 에이전트입니다. 사용자의 질문에 한국어로 답변하세요.",
                    "",
                    f"[현재 상태] {state.context_string()}",
                ]

                if state.csv_summary:
                    context_parts.append("")
                    context_parts.append("[업로드된 데이터 요약]")
                    context_parts.append(state.csv_summary[:3000])

                if state.last_analysis_report:
                    context_parts.append("")
                    context_parts.append("[분석 리포트]")
                    context_parts.append(state.last_analysis_report[:2000])

                context_parts.append("")
                context_parts.append(f"[사용자 질문] {message}")

                context = "\n".join(context_parts)

                response = await asyncio.to_thread(
                    self.model.generate_content, context
                )
                reply_text = response.text.strip()
                reply_text = self._extract_text_from_llm(reply_text)
                return {
                    "type": "text",
                    "text": reply_text,
                    "data": None,
                    "options": self._build_next_options(state)
                }
            except Exception as e:
                logger.error(f"General LLM error: {e}")

        return {
            "type": "text",
            "text": "요청을 처리하는 중 문제가 발생했습니다. 다시 시도해 주세요.",
            "data": None,
            "options": self._build_next_options(session.state)
        }

    async def _skill_ask_for_data(
        self, session: CrewSession, project_id: str, message: str, params: Dict
    ) -> Dict:
        question = params.get("question", "추가 데이터가 필요합니다.")
        return {
            "type": "text",
            "text": f"📋 {question}",
            "data": None,
            "options": [{"label": "📁 파일 업로드", "action": "upload"}],
        }

    # ----------------------------------------------------------
    # 특수 핸들러: 리셋 / 가이드 / 도메인 변경
    # ----------------------------------------------------------
    async def _handle_reset(self, session: CrewSession, project_id: str, message: str) -> Dict:
        session.state = SessionState()
        session.history.clear()
        return {
            "type": "system",
            "text": "🔄 **모든 상태가 초기화되었습니다.** 파일 업로드부터 다시 시작해 주세요.",
            "data": None,
            "options": [
                {"label": "📁 파일 업로드", "action": "upload"},
                {"label": "📖 가이드", "action": "send", "message": "가이드"},
            ],
        }

    async def _handle_guide(self, session: CrewSession, project_id: str, message: str) -> Dict:
        state = session.state
        guide_text = self._build_guide_text(state)
        return {
            "type": "guide",
            "text": guide_text,
            "data": None,
            "options": self._build_next_options(state)
        }

    async def _handle_domain_change(self, session: CrewSession, project_id: str, message: str) -> Dict:
        new_domain = InputClassifier.extract_domain_from_message(message)
        if new_domain:
            session.state.domain_override = new_domain
            session.state.detected_domain = new_domain
            session.state.domain_confidence = 1.0
            display = self._domain_display(new_domain)
            return {
                "type": "system",
                "text": f"🔄 도메인이 **{display}**(으)로 변경되었습니다.",
                "data": None,
                "options": [
                    {"label": "📊 다시 분석", "action": "send", "message": "분석 시작해줘"},
                    {"label": "⏭️ 그대로 진행", "action": "send", "message": "솔버 추천해줘"},
                ],
            }
        return {
            "type": "system",
            "text": "변경할 도메인을 지정해 주세요.\n\n지원: 항공, 철도, 버스, 물류, 병원",
            "data": None,
            "options": [
                {"label": "✈️ 항공", "action": "send", "message": "도메인 변경 항공"},
                {"label": "🚄 철도", "action": "send", "message": "도메인 변경 철도"},
                {"label": "🚌 버스", "action": "send", "message": "도메인 변경 버스"},
                {"label": "📦 물류", "action": "send", "message": "도메인 변경 물류"},
                {"label": "🏥 병원", "action": "send", "message": "도메인 변경 병원"},
            ],
        }
    # ----------------------------------------------------------
    # 유틸리티
    # ----------------------------------------------------------

    def _build_facts_summary(self, facts: dict) -> str:
        """팩트 데이터를 프롬프트용 텍스트로 변환"""
        if not facts:
            return ""

        lines = ["[VERIFIED DATA FACTS - 코드로 계산된 확정값, 절대 변경 금지]"]
        lines.append(f"총 파일 수: {len(facts.get('files', []))}개")
        lines.append(f"총 레코드 수: {facts.get('total_records', 0):,}개")

        for f in facts.get("files", []):
            lines.append(f"\n파일: {f['name']} ({f['type']})")
            lines.append(f"  레코드 수: {f.get('records', 0):,}개")
            if f.get('columns') and isinstance(f['columns'], list):
                lines.append(f"  컬럼 수: {len(f['columns'])}개")

        # 시트 정보
        for filename, sheets in facts.get("sheet_info", {}).items():
            for sheet_name, info in sheets.items():
                lines.append(f"  [{filename} → {sheet_name}] 행: {info['rows']:,}, 열: {info['cols']}")

        # 주요 고유값 수 (집합 크기 추정에 활용)
        unique = facts.get("unique_counts", {})
        if unique:
            lines.append("\n[주요 컬럼별 고유값 수 - 집합(Set) 크기 산정 근거]")
            for key, count in sorted(unique.items(), key=lambda x: -x[1])[:20]:
                lines.append(f"  {key}: {count:,}개")

        return "\n".join(lines)

    def _clean_report(self, raw: str) -> str:
        """내부 지시문 제거"""
        cleaned = re.sub(r'^⛔.*$', '', raw, flags=re.MULTILINE)
        cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'\[내부\s*검증[^\]]*\].*?(?=\n##|\n---|\Z)', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'SYSTEM[- ]?LOCKED.*?(?=\n##|\n---|\Z)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'^.*절대\s*변경하지\s*마.*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^.*출력을\s*종료하세요.*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _extract_text_from_llm(self, text: str) -> str:
        """LLM 응답에서 JSON을 제거하고 자연어 텍스트만 추출"""
        # JSON 블록 제거
        cleaned = re.sub(r'```json\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
        cleaned = re.sub(r'\{[^{}]*"tool_code"[^{}]*\}', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'\{[^{}]*"tool_name"[^{}]*\}', '', cleaned, flags=re.DOTALL)
        # ★ 추가: 스킬명 패턴 제거
        skill_names = [
            "AnalyzeDataSkill", "MathModelSkill", "PreDecisionSkill",
            "StartOptimizationSkill", "ShowResultSkill", "AnswerQuestionSkill",
            "GeneralReplySkill", "FileReceivedSkill", "UpdateWorkspaceSkill",
            "AskForDataSkill",
        ]
        for skill in skill_names:
            cleaned = cleaned.replace(skill, "")
        # "~실행", "~수행" 패턴 제거
        cleaned = re.sub(r'`[^`]*Skill`\s*실행\.?', '', cleaned)
        cleaned = re.sub(r'[A-Za-z]+Skill\s*실행\.?', '', cleaned)
        cleaned = re.sub(r'[A-Za-z]+Skill\s*수행\.?', '', cleaned)

        cleaned = self._clean_report(cleaned)
        cleaned = self._clean_report(cleaned)
        if not cleaned.strip():
            return "무엇을 도와드릴까요? 아래 버튼을 눌러 다음 단계를 진행해 보세요."
        return cleaned

    def _domain_display(self, domain: Optional[str]) -> str:
        display_map = {
            "aviation": "✈️ 항공 (Aviation)",
            "railway": "🚄 철도 (Railway)",
            "bus": "🚌 버스 (Bus)",
            "logistics": "📦 물류 (Logistics)",
            "hospital": "🏥 병원 (Hospital)",
            "general": "🔧 일반 (General)",
        }
        return display_map.get(domain, f"🔧 {domain or '미감지'}")

    def _build_guide_text(self, state: SessionState) -> str:
        lines = ["📖 **워크플로 가이드**\n"]
        steps = [
            ("1️⃣", "파일 업로드", state.file_uploaded),
            ("2️⃣", "데이터 분석", state.analysis_completed),
            ("3️⃣", "수학 모델 생성", state.math_model_confirmed),
            ("4️⃣", "솔버 추천", state.pre_decision_done),
            ("5️⃣", "최적화 실행", state.optimization_done),
        ]
        for icon, label, done in steps:
            status = "✅" if done else "⬜"
            lines.append(f"{icon} {status} {label}")
        lines.append(f"\n현재 상태: {state.context_string()}")
        return "\n".join(lines)

    def _build_next_options(self, state: SessionState) -> List[Dict]:
        if not state.file_uploaded:
            return [
                {"label": "📁 파일 업로드", "action": "upload"},
                {"label": "📖 가이드", "action": "send", "message": "가이드"},
            ]
        if not state.analysis_completed:
            return [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}]
        if not state.math_model_confirmed:
            return [
                {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
                {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
            ]
        if not state.pre_decision_done:
            return [
                {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                {"label": "📐 수학 모델", "action": "send", "message": "수학 모델 보여줘"},
            ]
        if not state.optimization_done:
            return [
                {"label": "🚀 최적화 실행", "action": "send", "message": "최적화 실행해줘"},
                {"label": "⚡ 솔버 결과", "action": "send", "message": "솔버 결과 보여줘"},
            ]
        return [
            {"label": "📈 최적화 결과", "action": "send", "message": "최적화 결과 보여줘"},
            {"label": "📥 다운로드", "action": "download"},
            {"label": "🔙 처음부터", "action": "send", "message": "리셋"},
        ]

    def _error_response(self, text: str, retry_msg: str = "다시 시도") -> Dict:
        return {
            "type": "error",
            "text": f"❌ {text}",
            "data": None,
            "options": [
                {"label": "🔄 다시 시도", "action": "send", "message": retry_msg},
                {"label": "📖 가이드", "action": "send", "message": "가이드"},
            ],
        }


# ============================================================
# 싱글턴
# ============================================================
crew_agent = CrewAgent()