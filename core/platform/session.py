"""
core/platform/session.py
─────────────────────────
플랫폼 공통 세션 상태 관리 모듈 (도메인 무관).

SessionState: 프로젝트별 워크플로 상태 (파일 업로드, 분석, 모델링, 솔버, 실행)
CrewSession: 세션 상태 + 대화 히스토리를 묶는 컨테이너
save/load_session_state: DB 직렬화/역직렬화
get_session: 프로젝트 ID로 세션 조회 (없으면 DB에서 복원 후 생성)

원래 domains/crew/session.py에서 core/platform/으로 이동.
기존 import 경로는 re-export wrapper로 호환 유지.
"""
from __future__ import annotations

import json
import logging
import os
import asyncio
import time as _time_mod
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.database import SessionLocal
from core.models import SessionStateDB

logger = logging.getLogger(__name__)

# ── DB ↔ SessionState 필드 매핑 ──
# (field_name, serialization_type)
#   "direct" = 값 그대로 전달 (bool, str, int)
#   "json"   = JSON 직렬화/역직렬화 (Dict, List)
_DB_FIELD_SPEC: list[tuple[str, str]] = [
    # Pipeline flags
    ("file_uploaded", "direct"),
    ("analysis_completed", "direct"),
    ("math_model_confirmed", "direct"),
    ("pre_decision_done", "direct"),
    ("optimization_done", "direct"),
    # Problem Definition
    ("problem_defined", "direct"),
    ("problem_definition", "json"),
    ("confirmed_problem", "json"),
    # Data Normalization
    ("data_normalized", "direct"),
    ("normalization_mapping", "json"),
    ("normalized_data_summary", "json"),
    # Structural Normalization
    ("structural_normalization_done", "direct"),
    ("phase1_summary", "json"),
    # Constraints
    ("constraints_confirmed", "direct"),
    ("confirmed_constraints", "json"),
    # Version pointers
    ("current_dataset_version_id", "direct"),
    ("current_model_version_id", "direct"),
    ("current_run_id", "direct"),
    # Domain
    ("detected_domain", "direct"),
    # Strings
    ("solver_selected", "direct"),
    ("csv_summary", "direct"),
    ("last_analysis_report", "direct"),
    # JSON blobs
    ("uploaded_files", "json"),
    ("math_model", "json"),
    ("last_pre_decision_result", "json"),
    ("last_optimization_result", "json"),
    ("data_facts", "json"),
    ("pending_param_inputs", "json"),
    ("clarification_answers", "json"),
    ("pending_clarifications", "json"),
    ("clarification_done", "direct"),
    # Pending actions (목적함수/카테고리 변경 중간 상태)
    ("objective_changing", "direct"),
    ("pending_objective", "json"),
    ("pending_extra_instructions", "direct"),
    ("pending_category_change", "json"),
]

@dataclass
class SessionState:
    """프로젝트별 워크플로 상태"""
    project_id: Optional[str] = None
    file_uploaded: bool = False
    analysis_completed: bool = False
    pre_decision_done: bool = False
    solver_selected: Optional[str] = None
    optimization_done: bool = False

    # ── Problem Definition Phase ──
    problem_definition_proposed: bool = False
    problem_definition: Optional[Dict] = None
    problem_defined: bool = False
    confirmed_problem: Optional[Dict] = None

    # ── Data Normalization Phase ──
    normalization_mapping: Optional[Dict] = None
    normalization_confirmed: bool = False
    data_normalized: bool = False
    normalized_data_summary: Optional[Dict] = None

    # ── Structural Normalization Phase 1 ──
    structural_normalization_done: bool = False
    phase1_summary: Optional[Dict] = None

    # ── Constraint Confirmation (TASK 3) ──
    constraints_confirmed: bool = False
    objective_changing: bool = False  # 목적함수 변경 진행 중
    confirmed_constraints: Optional[Dict] = None
    pending_objective: Optional[Dict] = None          # {"name": str, "data": dict}
    pending_extra_instructions: Optional[str] = None  # 목적함수 변경 시 추가 지시사항
    pending_category_change: Optional[Dict] = None    # {"constraint": str, "to": str}

    # ── Ambiguity Clarification ──
    clarification_answers: Optional[Dict] = None      # {question_id: answer}
    pending_clarifications: Optional[List[Dict]] = None  # 미응답 질문 목록
    clarification_done: bool = False



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
    pending_param_inputs: Optional[List[str]] = None
    last_report: Optional[str] = None
    data_facts: Optional[Dict] = None  # ★ 추가: 코드로 계산된 정확한 팩트 데이터 
    data_profile: Optional[Dict] = None  # Gate1 column profile

    def reset_from_math_model(self):
        """수학 모델 재생성 시 후속 단계 모두 초기화"""
        self.math_model = None
        self.math_model_confirmed = False
        self.pending_param_inputs = None
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
        self.problem_definition_proposed = False
        self.problem_definition = None
        self.problem_defined = False
        self.confirmed_problem = None
        self.normalization_mapping = None
        self.normalization_confirmed = False
        self.data_normalized = False
        self.normalized_data_summary = None

        self.structural_normalization_done = False
        self.phase1_summary = None
        self.constraints_confirmed = False
        self.confirmed_constraints = None

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
    """세션 상태를 DB에 저장 (_DB_FIELD_SPEC 기반 자동 직렬화)"""
    db = SessionLocal()
    try:
        pid = int(project_id)
        row = db.query(SessionStateDB).filter(SessionStateDB.project_id == pid).first()
        if not row:
            row = SessionStateDB(project_id=pid)
            db.add(row)

        for field_name, ser_type in _DB_FIELD_SPEC:
            value = getattr(state, field_name, None)
            if ser_type == "json" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            setattr(row, field_name, value)

        # domain_confidence: float → DB에 그대로 저장
        row.domain_confidence = state.domain_confidence

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save session state for project {project_id}: {e}")
    finally:
        db.close()


def load_session_state(project_id: str) -> Optional[SessionState]:
    """DB에서 세션 상태를 복원 (_DB_FIELD_SPEC 기반 자동 역직렬화)"""
    db = SessionLocal()
    try:
        pid = int(project_id)
        row = db.query(SessionStateDB).filter(SessionStateDB.project_id == pid).first()
        if not row:
            return None

        state = SessionState()
        state.project_id = str(project_id)

        for field_name, ser_type in _DB_FIELD_SPEC:
            db_val = getattr(row, field_name, None)
            if ser_type == "json":
                if db_val:
                    setattr(state, field_name, json.loads(db_val))
                # else: dataclass 기본값 유지
            else:
                if db_val is not None:
                    setattr(state, field_name, db_val)
                # else: dataclass 기본값 유지 (bool=False, Optional=None 등)

        # 파생 필드: DB에 별도 컬럼 없이 다른 필드에서 복원
        state.problem_definition_proposed = state.problem_defined
        state.normalization_confirmed = state.data_normalized

        # domain_confidence: DB에서 str로 저장될 수 있음
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


# ── LRU + TTL 세션 캐시 ──────────────────────────────────

_MAX_SESSIONS = int(os.environ.get("SESSION_CACHE_MAX", "100"))
_SESSION_TTL_SEC = int(os.environ.get("SESSION_TTL_SEC", "3600"))


class _SessionCache:
    """LRU + TTL 세션 캐시. 최대 _MAX_SESSIONS개, TTL 초과 시 DB 저장 후 제거."""

    def __init__(self, max_size: int = _MAX_SESSIONS, ttl: int = _SESSION_TTL_SEC):
        self._cache: OrderedDict[str, tuple[CrewSession, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Optional[CrewSession]:
        if key in self._cache:
            session, ts = self._cache[key]
            if _time_mod.time() - ts > self._ttl:
                # TTL 만료 → DB에 저장 후 제거
                save_session_state(key, session.state)
                del self._cache[key]
                logger.info(f"[{key}] Session evicted (TTL expired)")
                return None
            # LRU: 최근 사용으로 이동
            self._cache.move_to_end(key)
            self._cache[key] = (session, _time_mod.time())
            return session
        return None

    def put(self, key: str, session: CrewSession):
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = (session, _time_mod.time())
            return
        if len(self._cache) >= self._max_size:
            # 가장 오래된 항목 제거 (DB에 저장 후)
            old_key, (old_session, _) = self._cache.popitem(last=False)
            save_session_state(old_key, old_session.state)
            logger.info(f"[{old_key}] Session evicted (LRU, cache full: {self._max_size})")
        self._cache[key] = (session, _time_mod.time())

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def __len__(self) -> int:
        return len(self._cache)


_sessions = _SessionCache()


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


def merge_background_update(project_id: str, **updates):
    """Background thread에서 변경된 필드를 LRU 캐시 세션에 반영.

    Background job(tasks.py → post_processing.py)은 별도 스레드에서
    DB만 업데이트하므로, LRU 캐시의 세션 객체는 갱신되지 않는다.
    이 함수로 캐시도 동기화하여, 이후 채팅 요청에서 최신 상태를 반환.
    """
    cached = _sessions.get(project_id)
    if cached is not None:
        for key, value in updates.items():
            setattr(cached.state, key, value)
        logger.info(f"[{project_id}] Merged background update into cache: {list(updates.keys())}")


def get_session(project_id: str) -> CrewSession:
    cached = _sessions.get(project_id)
    if cached is not None:
        return cached

    session = CrewSession()
    # DB에서 이전 상태 복원 시도
    saved_state = load_session_state(project_id)
    if saved_state:
        session.state = saved_state
        logger.info(f"[{project_id}] Session state restored from DB")
    # DB에서 대화 히스토리 복원
    _restore_history_from_db(project_id, session)
    _sessions.put(project_id, session)
    return session

