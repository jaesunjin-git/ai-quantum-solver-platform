# ============================================================
# chat/chat_service.py — v3.0
# ============================================================
# 변경 이력:
#   v1.0 : 초기 라우터
#   v2.0 : 프로젝트 타입 DB 조회, 이벤트 처리
#   v3.0 :
#     - crew_agent.run() 호출 시 파라미터명 정합 (user_message → message)
#     - 파일 이벤트에서 event_type/event_data를 agent.run()에 전달
#     - event_type 비교를 대소문자 무관하게 통일
#     - router.py의 "file_upload" / 프론트의 "FILES_UPLOADED" 양쪽 호환
# ============================================================

import logging
import asyncio
from typing import Optional, Dict, Any

from google import genai
from sqlalchemy.orm import Session

from core.config import settings
from core.models import ProjectDB

# Agent
from domains.crew.agent import crew_agent

# Legacy Domains
from domains.logistics.service import handle_logistics
from domains.finance.service import handle_finance
from domains.material.service import handle_material
from domains.general.service import handle_general_consulting

logger = logging.getLogger(__name__)

# ── Gemini Client ──────────────────────────────────────────
_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        except Exception as e:
            logger.error(f"Gemini Client 초기화 실패: {e}")
    return _client


# ── 헬퍼 ───────────────────────────────────────────────────
def _finalize_response(result_dict: dict) -> dict:
    """응답 데이터에 누락 필드를 기본값으로 채워주는 헬퍼"""
    if not isinstance(result_dict, dict):
        result_dict = {"type": "text", "text": str(result_dict)}
    result_dict.setdefault("data", None)
    result_dict.setdefault("type", "text")
    result_dict.setdefault("options", [])

    # ── Auto-inject stage validation based on view_mode ──
    _inject_stage_validation(result_dict)

    return result_dict


# view_mode → validation stage mapping
_VIEW_MODE_STAGE = {
    "problem_defined": 3,
    "problem_definition": 3,
    "normalization_mapping": 4,
    "normalization_complete": 4,
}


def _inject_stage_validation(result_dict: dict) -> None:
    """Run stage validation if the response's view_mode maps to a validatable stage."""
    data = result_dict.get("data")
    if not isinstance(data, dict):
        return
    if data.get("validation"):
        return  # Already has validation (e.g., post-solve injects its own)

    view_mode = data.get("view_mode", "")
    stage = _VIEW_MODE_STAGE.get(view_mode)
    if stage is None:
        return

    try:
        from engine.validation.registry import get_registry
        registry = get_registry()

        # Build context from data
        context = {}
        if stage == 3:
            # Problem definition stage
            confirmed = data.get("confirmed_problem") or data.get("proposal", {})
            context["parameters"] = confirmed.get("parameters", {})
            context["domain"] = confirmed.get("domain", data.get("domain", settings.DEFAULT_DOMAIN))
            context["confirmed_problem"] = confirmed
        elif stage == 4:
            # Normalization stage
            context["mappings"] = data.get("mappings", {})
            context["results"] = data.get("results", [])
            context["errors"] = data.get("errors", [])
            context["original_stats"] = data.get("original_stats", {})
            context["normalized_stats"] = data.get("normalized_stats", {})

        stage_result = registry.run_stage(stage, context)
        if stage_result.items:
            data["validation"] = stage_result.to_dict()
    except Exception as e:
        logger.warning(f"Stage validation injection failed: {e}")


def _resolve_project_type(db: Session, project_id: str) -> str:
    """DB에서 프로젝트 타입 조회 (Single Source of Truth)"""
    project = db.query(ProjectDB).filter(ProjectDB.id == project_id).first()
    if not project:
        logger.warning(f"Project ID '{project_id}' not found in DB → fallback to 'general'")
        return "general"
    return project.type.lower().strip()


def _is_file_event(event_type: Optional[str]) -> bool:
    """router.py / 프론트엔드 양쪽의 이벤트명을 모두 허용"""
    if not event_type:
        return False
    normalized = event_type.lower().replace("-", "_").strip()
    return normalized in ("file_upload", "files_uploaded", "file_uploaded")


# ── 메인 라우터 ────────────────────────────────────────────
async def process_user_intents(
    db: Session,
    user_message: str,
    project_id: str,
    user_id: str,
    event_type: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    current_tab: Optional[str] = None,
):
    """
    사용자의 메시지 또는 이벤트를 처리하는 핵심 라우터.
    DB 기반으로 프로젝트 타입을 결정하며, 이벤트(파일 업로드 등)를 처리합니다.
    """

    # =============================================================
    # 1. 프로젝트 타입 결정
    # =============================================================
    real_project_type = _resolve_project_type(db, project_id)

    # =============================================================
    # 2. 시스템 이벤트 처리 (파일 업로드 등)
    # =============================================================
    if _is_file_event(event_type):
        return await _handle_file_event(
            real_project_type, event_data, project_id
        )

    # =============================================================
    # 3. 메시지 라우팅
    # =============================================================

    # [Crew Scheduling] — Agent 기반
    if real_project_type in ("crew", "crew_scheduling"):
        result = await crew_agent.run(
            message=user_message,
            project_id=project_id,
            has_file=False,
            current_tab=current_tab,
            event_type=event_type,
            event_data=event_data,
        )
        return _finalize_response(result)

    # [Legacy Domains] — 동기 함수 → thread로 실행
    if real_project_type == "logistics":
        result = await asyncio.to_thread(handle_logistics, user_message)
        return _finalize_response(result)

    if real_project_type == "finance":
        result = await asyncio.to_thread(handle_finance, user_message)
        return _finalize_response(result)

    if real_project_type == "material":
        result = await asyncio.to_thread(handle_material, user_message)
        return _finalize_response(result)

    # =============================================================
    # 4. Fallback — General Chat
    # =============================================================
    return await _handle_general_fallback(user_message, real_project_type)


# ── 파일 업로드 이벤트 ─────────────────────────────────────
async def _handle_file_event(
    project_type: str,
    event_data: Optional[Dict[str, Any]],
    project_id: str,
) -> dict:
    """파일 업로드 이벤트를 처리합니다."""
    files = (event_data or {}).get("files", [])

    # uploaded_files 키도 지원 (router.py 구조에 따라)
    if not files:
        files = (event_data or {}).get("uploaded_files", [])

    if project_type in ("crew", "crew_scheduling"):
        # agent.run()에 event_type + event_data를 직접 전달
        # → agent._handle_file_upload가 파일 목록을 정확히 수신
        result = await crew_agent.run(
            message="",
            project_id=project_id,
            has_file=True,
            current_tab=None,
            event_type="file_upload",
            event_data={"files": files},        # ← 추가: 파일 목록 전달
        )
        return _finalize_response(result)

    # 다른 도메인은 기본 응답
    file_names = ", ".join(f.get("filename", "unknown") for f in files)
    return {
        "type": "text",
        "text": f"파일 {len(files)}개가 업로드되었습니다: {file_names}",
        "data": None,
        "options": [],
    }


# ── General Fallback (LLM) ────────────────────────────────
async def _handle_general_fallback(
    user_message: str,
    project_type: str,
) -> dict:
    """도메인 미지정 시 Gemini LLM으로 일반 대화 처리"""
    response = {
        "type": "text",
        "text": "",
        "data": None,
        "options": [],
    }

    client = _get_client()
    if client is None:
        response["text"] = "AI 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
        return response

    try:
        context = ""
        if project_type != "general":
            context = f" (Current Project Type: {project_type})"

        prompt = (
            f"{user_message}{context} "
            f"(Answer in Korean, polite business tone)"
        )

        ai_response = await asyncio.to_thread(
            client.models.generate_content,
            model=settings.MODEL_CHAT,
            contents=prompt,
        )

        response["text"] = (
            ai_response.text
            if ai_response.text
            else "죄송합니다. 응답을 생성할 수 없습니다."
        )

    except Exception as e:
        logger.error(f"General fallback LLM 오류: {e}", exc_info=True)
        response["text"] = "일시적인 시스템 오류가 발생했습니다."

    return response