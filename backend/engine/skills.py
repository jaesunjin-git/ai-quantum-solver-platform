from pydantic import BaseModel, Field
from typing import Union, Literal, Optional, List

# 1. 화면 업데이트
class UpdateWorkspaceSkill(BaseModel):
    action: Literal["update_schedule", "change_shifts", "show_analysis"] = Field(..., description="Action type")
    details: str = Field(..., description="Details")
    report_markdown: Optional[str] = Field(None, description="Markdown report")

# 2. 데이터 요청
class AskForDataSkill(BaseModel):
    question: str = Field(..., description="Question to ask")
    required_file_type: Optional[str] = Field(None, description="File type")

# 3. 데이터 분석 (리포트용)
class AnalyzeDataSkill(BaseModel):
    target: str = Field(..., description="Target data")
    analysis_focus: str = Field(..., description="Focus")

# 4. 파일 수신 확인
class FileReceivedSkill(BaseModel):
    received_files: str = Field(..., description="List of filenames")
    suggested_actions: List[str] = Field(..., description="List of suggested next steps")
    message: str = Field(..., description="Message to the user")

# 5. [명칭 변경] 최적화 준비 (Pre-Decision Engine)
class PreDecisionSkill(BaseModel):
    """
    Use this tool when the user wants to start the optimization process (Simulation/Recommendation).
    Triggers: Pre-Decision Engine.
    """
    goal: str = Field("General", description="Optimization goal")

# 6. [명칭 변경] 최적화 시작 (Auto-Compiler & Execution)
class StartOptimizationSkill(BaseModel):
    """
    Use this tool when the user selects a solver and confirms execution.
    Triggers: Auto-Compiler & Hybrid Orchestrator.
    """
    selected_solver: str = Field(..., description="Selected solver name")

# 🌟 7. [신규 추가] 결과 조회 (누락되었던 부분!)
class ShowResultSkill(BaseModel):
    """
    Use this tool to show the final optimization results again.
    """
    summary: str = Field(..., description="Summary of the result")
    kpi_data: dict = Field(..., description="KPI Data")

# 8. 질문 답변
class AnswerQuestionSkill(BaseModel):
    answer: str = Field(..., description="Answer")

# 9. 일반 대화
class GeneralReplySkill(BaseModel):
    message: str = Field(..., description="Message")

# 🌟 Union 업데이트 (모든 스킬이 다 들어있는지 확인)
CrewTools = Union[
    UpdateWorkspaceSkill, 
    AskForDataSkill, 
    AnalyzeDataSkill,
    FileReceivedSkill,
    PreDecisionSkill,       # (구 RunOptimization)
    StartOptimizationSkill, # (구 ExecuteOptimization)
    ShowResultSkill,        # 🌟 추가됨
    AnswerQuestionSkill, 
    GeneralReplySkill
]