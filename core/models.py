from sqlalchemy import Float,  func,  Column, Integer, String, Text, Boolean, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from .database import Base
import datetime

# 0. [Core] 사용자 인증
class UserDB(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(String, default="user")  # "admin" | "user"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))


# 1. [Domain] 비즈니스 로직 관련
class MenuDB(Base):
    __tablename__ = "menus"
    __table_args__ = {"schema": "core"}  # 👈 핵심: core 스키마
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String, index=True)
    label = Column(String)
    icon_key = Column(String)
    path = Column(String)

class ScenarioDB(Base):
    __tablename__ = "scenarios"
    __table_args__ = {"schema": "domain"} # 👈 domain 스키마
    id = Column(Integer, primary_key=True, index=True)
    task_key = Column(String, unique=True, index=True)
    config = Column(JSON)

class ProblemTemplateDB(Base):
    __tablename__ = "problem_templates"
    __table_args__ = {"schema": "domain"} # 👈 domain 스키마
    id = Column(Integer, primary_key=True, index=True)
    task_key = Column(String, unique=True, index=True)
    math_model_type = Column(String)
    decision_rules = Column(JSON)
    supported_algorithms = Column(JSON)

# 2. [Core] 프로젝트 (가장 중심)
class ProjectDB(Base):
    __tablename__ = "projects"
    __table_args__ = {"schema": "core"} # 👈 core 스키마
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    type = Column(String)
    owner = Column(String, index=True)
    status = Column(String, default="In Progress")
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    # 관계 설정
    chats = relationship("ChatHistoryDB", back_populates="project")
    jobs = relationship("JobDB", back_populates="project")

# 3. [Chat] 대화 기록
class ChatHistoryDB(Base):
    __tablename__ = "chat_history"
    __table_args__ = {"schema": "chat"} # 👈 chat 스키마
    
    id = Column(Integer, primary_key=True, index=True)
    # ⚠️ 중요: 다른 스키마의 테이블을 참조할 땐 '스키마명.테이블명.컬럼'
    project_id = Column(Integer, ForeignKey("core.projects.id")) 
    
    role = Column(String)
    message_type = Column(String)
    message_text = Column(Text)
    card_json = Column(Text, nullable=True)
    options_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    project = relationship("ProjectDB", back_populates="chats")

# 4. [Job] 실행 작업
class JobDB(Base):
    __tablename__ = "jobs"
    __table_args__ = {"schema": "job"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"))

    status = Column(String, default="PENDING")  # PENDING, RUNNING, COMPLETE, FAILED
    backend = Column(String)
    solver_id = Column(String, nullable=True)
    solver_name = Column(String, nullable=True)
    model_version_id = Column(Integer, nullable=True)  # 실행 시점의 모델 버전 (고정)
    dataset_version_id = Column(Integer, nullable=True)  # 실행 시점의 데이터 버전 (고정)
    progress = Column(String, nullable=True)     # 진행 상태 메시지
    progress_pct = Column(Integer, nullable=True)  # 0~100 진행률
    error = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    celery_task_id = Column(String, nullable=True)    # Celery 태스크 ID (취소용)
    compare_group_id = Column(String, nullable=True)  # Compare 모드 그룹 ID
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("ProjectDB", back_populates="jobs")

# 5. [Core] 세션 상태 영속화
class SessionStateDB(Base):
    __tablename__ = "session_states"
    __table_args__ = {"schema": "core"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"), unique=True, index=True)

    # Version pointers
    current_dataset_version_id = Column(Integer, nullable=True)
    current_model_version_id = Column(Integer, nullable=True)
    current_run_id = Column(Integer, nullable=True)

    # 파이프라인 진행 상태
    file_uploaded = Column(Boolean, default=False)
    analysis_completed = Column(Boolean, default=False)
    math_model_confirmed = Column(Boolean, default=False)
    pre_decision_done = Column(Boolean, default=False)
    optimization_done = Column(Boolean, default=False)

    # 캐시 데이터 (JSON 텍스트로 저장)
    uploaded_files = Column(Text, nullable=True)       # JSON string: [{"name":...}, ...]
    csv_summary = Column(Text, nullable=True)
    last_analysis_report = Column(Text, nullable=True)
    math_model = Column(Text, nullable=True)           # JSON string
    last_pre_decision_result = Column(Text, nullable=True)  # JSON string
    last_optimization_result = Column(Text, nullable=True)  # JSON string
    data_facts = Column(Text, nullable=True)                # ★ JSON string
    solver_selected = Column(String, nullable=True)         # 선택된 솔버 이름/ID
    pending_param_inputs = Column(Text, nullable=True)      # JSON string: 파라미터 입력 대기 목록
    clarification_answers = Column(Text, nullable=True)    # JSON string: 모호성 질문 답변
    pending_clarifications = Column(Text, nullable=True)   # JSON string: 대기 중 질문 목록
    clarification_done = Column(Boolean, default=False)

    # Problem Definition
    problem_defined = Column(Boolean, default=False)
    problem_definition = Column(Text, nullable=True)
    confirmed_problem = Column(Text, nullable=True)

    # Data Normalization
    data_normalized = Column(Boolean, default=False)
    normalization_mapping = Column(Text, nullable=True)
    normalized_data_summary = Column(Text, nullable=True)

    # Structural Normalization (Phase 1) – TASK 2-A
    structural_normalization_done = Column(Boolean, default=False)
    phase1_summary = Column(Text, nullable=True)

    # Constraint Confirmation – TASK 3
    constraints_confirmed = Column(Boolean, default=False)
    confirmed_constraints = Column(Text, nullable=True)

    # Pending actions (목적함수/카테고리 변경 중간 상태)
    objective_changing = Column(Boolean, default=False)
    pending_objective = Column(Text, nullable=True)          # JSON string
    pending_extra_instructions = Column(String, nullable=True)
    pending_category_change = Column(Text, nullable=True)    # JSON string

    # 도메인 정보
    detected_domain = Column(String, nullable=True)
    domain_confidence = Column(String, nullable=True)

    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    project = relationship("ProjectDB")

class SolverSettingDB(Base):
    __tablename__ = "solver_settings"
    __table_args__ = {"schema": "core"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    solver_id = Column(String, unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=False)
    api_key = Column(String, nullable=True)               # deprecated: 평문 (하위 호환)
    encrypted_api_key = Column(String, nullable=True)      # Fernet 암호화된 API Key
    time_limit_sec = Column(Integer, nullable=True)  # NULL이면 YAML max_time_seconds 사용
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_by = Column(String, nullable=True)

    def get_api_key(self) -> str | None:
        """복호화된 API Key 반환 (encrypted 우선, 평문 fallback)."""
        if self.encrypted_api_key:
            from core.crypto import decrypt
            try:
                return decrypt(self.encrypted_api_key)
            except Exception:
                pass
        return self.api_key

    def set_api_key(self, plaintext: str | None):
        """API Key를 암호화하여 저장. 평문 컬럼은 비움."""
        if plaintext:
            from core.crypto import encrypt
            self.encrypted_api_key = encrypt(plaintext)
            self.api_key = None  # 평문 제거
        else:
            self.encrypted_api_key = None
            self.api_key = None


# ============================================================
# Version Management Tables
# ============================================================

class DatasetVersionDB(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = {"schema": "data"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    domain_type = Column(String, nullable=True)
    file_hash = Column(String, nullable=True)
    file_list = Column(Text, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("ProjectDB")


class ModelVersionDB(Base):
    __tablename__ = "model_versions"
    __table_args__ = {"schema": "model"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"), nullable=False, index=True)
    dataset_version_id = Column(Integer, ForeignKey("data.dataset_versions.id"), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    domain_type = Column(String, nullable=True)
    objective_type = Column(String, nullable=True)
    objective_summary = Column(String, nullable=True)
    model_json = Column(Text, nullable=True)
    variable_count = Column(Integer, nullable=True)
    constraint_count = Column(Integer, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("ProjectDB")
    dataset_version = relationship("DatasetVersionDB")


class RunResultDB(Base):
    __tablename__ = "run_results"
    __table_args__ = {"schema": "job"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"), nullable=False, index=True)
    model_version_id = Column(Integer, ForeignKey("model.model_versions.id"), nullable=True)
    domain_type = Column(String, nullable=True)
    solver_id = Column(String, nullable=False)
    solver_name = Column(String, nullable=True)
    solver_params = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    objective_value = Column(Float, nullable=True)
    result_json = Column(Text, nullable=True)
    compile_time_sec = Column(Float, nullable=True)
    execute_time_sec = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("ProjectDB")
    model_version = relationship("ModelVersionDB")


# 9. [Core] Intent 분류 로그
class IntentLogDB(Base):
    __tablename__ = "intent_logs"
    __table_args__ = {"schema": "core"}

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("core.projects.id"), nullable=True, index=True)
    skill_name = Column(String, nullable=True)           # 분류 대상 스킬
    message = Column(Text, nullable=False)                # 사용자 원문 메시지
    intent = Column(String, nullable=False)               # 분류된 intent
    confidence = Column(Float, nullable=False, default=1.0)
    source = Column(String, nullable=False)               # fast_path | llm | fallback | quick_classify
    params_json = Column(Text, nullable=True)             # 추출된 파라미터 (JSON)
    pipeline_stage = Column(String, nullable=True)        # 분류 시점의 파이프라인 단계
    created_at = Column(DateTime, server_default=func.now())

