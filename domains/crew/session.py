"""
domains/crew/session.py
───────────────────────
세션 상태 관리 모듈.

SessionState: 프로젝트별 워크플로 상태 (파일 업로드, 분석, 모델링, 솔버, 실행)
CrewSession: 세션 상태 + 대화 히스토리를 묶는 컨테이너
save/load_session_state: DB 직렬화/역직렬화
get_session: 프로젝트 ID로 세션 조회 (없으면 DB에서 복원 후 생성)

리팩토링 Step 2에서 agent.py로부터 추출됨.
"""
from __future__ import annotations

import json
import logging
import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.database import SessionLocal
from core.models import SessionStateDB

logger = logging.getLogger(__name__)

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
        row.uploaded_files = json.dumps(state.uploaded_files, ensure_ascii=False) if state.uploaded_files else None
        row.csv_summary = state.csv_summary
        row.last_analysis_report = state.last_analysis_report
        row.math_model = json.dumps(state.math_model, ensure_ascii=False) if state.math_model else None
        row.last_pre_decision_result = json.dumps(state.last_pre_decision_result, ensure_ascii=False) if state.last_pre_decision_result else None
        row.last_optimization_result = json.dumps(state.last_optimization_result, ensure_ascii=False) if state.last_optimization_result else None
        row.solver_selected = state.solver_selected
        row.pending_param_inputs = json.dumps(state.pending_param_inputs, ensure_ascii=False) if state.pending_param_inputs else None

        # 팩트 데이터
        # Problem Definition
        row.problem_defined = getattr(state, 'problem_defined', False)
        row.problem_definition = json.dumps(state.problem_definition, ensure_ascii=False) if state.problem_definition else None
        row.confirmed_problem = json.dumps(state.confirmed_problem, ensure_ascii=False) if state.confirmed_problem else None
        # Data Normalization
        row.data_normalized = getattr(state, 'data_normalized', False)
        row.normalization_mapping = json.dumps(state.normalization_mapping, ensure_ascii=False) if state.normalization_mapping else None
        row.normalized_data_summary = json.dumps(state.normalized_data_summary, ensure_ascii=False) if state.normalized_data_summary else None

        # Structural Normalization Phase 1
        row.structural_normalization_done = getattr(state, 'structural_normalization_done', False)
        row.phase1_summary = json.dumps(state.phase1_summary, ensure_ascii=False) if getattr(state, 'phase1_summary', None) else None

        # Constraints
        row.constraints_confirmed = getattr(state, 'constraints_confirmed', False)
        row.confirmed_constraints = json.dumps(state.confirmed_constraints, ensure_ascii=False) if getattr(state, 'confirmed_constraints', None) else None

        row.data_facts = json.dumps(state.data_facts, ensure_ascii=False) if state.data_facts else None

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
        state.project_id = str(project_id)
        state.file_uploaded = row.file_uploaded or False
        state.analysis_completed = row.analysis_completed or False
        state.math_model_confirmed = row.math_model_confirmed or False
        state.pre_decision_done = row.pre_decision_done or False
        state.optimization_done = row.optimization_done or False

        state.uploaded_files = json.loads(row.uploaded_files) if row.uploaded_files else []
        state.csv_summary = row.csv_summary
        state.last_analysis_report = row.last_analysis_report
        state.math_model = json.loads(row.math_model) if row.math_model else None
        state.last_pre_decision_result = json.loads(row.last_pre_decision_result) if row.last_pre_decision_result else None
        state.last_optimization_result = json.loads(row.last_optimization_result) if row.last_optimization_result else None
        state.solver_selected = getattr(row, 'solver_selected', None)
        state.pending_param_inputs = json.loads(row.pending_param_inputs) if getattr(row, 'pending_param_inputs', None) else None

        # 팩트 데이터
        # 팩트 데이터
        state.data_facts = json.loads(row.data_facts) if row.data_facts else None

        # Problem Definition
        state.problem_defined = getattr(row, 'problem_defined', False) or False
        state.problem_definition_proposed = state.problem_defined  # DB에서 복원 시
        state.problem_definition = json.loads(row.problem_definition) if getattr(row, 'problem_definition', None) else None
        state.confirmed_problem = json.loads(row.confirmed_problem) if getattr(row, 'confirmed_problem', None) else None
        # Data Normalization
        state.data_normalized = getattr(row, 'data_normalized', False) or False
        state.normalization_confirmed = state.data_normalized
        state.normalization_mapping = json.loads(row.normalization_mapping) if getattr(row, 'normalization_mapping', None) else None
        state.normalized_data_summary = json.loads(row.normalized_data_summary) if getattr(row, 'normalized_data_summary', None) else None

        # Structural Normalization Phase 1
        state.structural_normalization_done = getattr(row, 'structural_normalization_done', False) or False
        state.phase1_summary = json.loads(row.phase1_summary) if getattr(row, 'phase1_summary', None) else None

        # Constraints
        state.constraints_confirmed = getattr(row, 'constraints_confirmed', False) or False
        state.confirmed_constraints = json.loads(row.confirmed_constraints) if getattr(row, 'confirmed_constraints', None) else None


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

