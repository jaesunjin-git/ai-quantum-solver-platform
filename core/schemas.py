from datetime import datetime
from typing import Optional, Dict, Any, Union, List

from pydantic import BaseModel, Field, ConfigDict


# 1. 메뉴 응답 스키마
class MenuResponse(BaseModel):
    id: int
    label: str
    icon_key: str
    path: str

    model_config = ConfigDict(from_attributes=True)


# 2. 프로젝트 생성 요청 스키마
class ProjectCreate(BaseModel):
    title: str
    type: str
    owner: str

# 프로젝트명 수정 스키마
class ProjectUpdate(BaseModel):
    title: str


# 3. 프로젝트 응답 스키마
class ProjectResponse(BaseModel):
    id: int
    title: str
    type: str
    status: str
    owner: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# 4. 채팅 요청 스키마
class ChatRequest(BaseModel):
    message: str = Field(..., description="User's input message")

    # 제품 관점에서는 int/UUID 등으로 고정 추천.
    # 당장 호환성 위해 Union 유지 가능.
    project_id: Union[int, str] = Field(..., description="Project ID")

    # 이벤트 트리거용(선택)
    event_type: Optional[str] = Field(default=None, description="Event type")
    event_data: Optional[Dict[str, Any]] = Field(default=None, description="Event payload")
    current_tab: Optional[str] = Field(default=None, description="Currently active tab: analysis, math_model, solver, result")


# (선택) 응답 스키마도 있으면 프론트/백 계약이 안정해집니다.
class ChatResponse(BaseModel):
    role: str = "assistant"
    type: str = "text"
    text: str
    data: Optional[Dict[str, Any]] = None
    options: Optional[List[Dict[str, str]]] = None
    
    model_config = ConfigDict(from_attributes=True)


# 5. Crew 관련 스키마 (필요 시 사용)
class CrewOptimizationEstimate(BaseModel):
    total_crews: int
    estimated_cost: float
    time_required: str


class CrewExecutionResult(BaseModel):
    status: str
    job_id: str
    message: str


# 9. Intent Log 응답
class IntentLogResponse(BaseModel):
    id: int
    project_id: Optional[int] = None
    skill_name: Optional[str] = None
    message: str
    intent: str
    confidence: float
    source: str
    params_json: Optional[str] = None
    pipeline_stage: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IntentLogStats(BaseModel):
    total: int
    by_source: Dict[str, int]
    by_intent: Dict[str, int]
    low_confidence_count: int
    avg_confidence: float


# Pydantic v2: forward-ref/지연평가가 섞여도 안전하게
ChatRequest.model_rebuild()
ChatResponse.model_rebuild()