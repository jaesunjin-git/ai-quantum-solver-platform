from __future__ import annotations
"""
domains/common/skills/handlers.py
───────────────────────────────
파일 업로드, 리셋, 가이드, 도메인 변경 등 워크플로 핸들러.

handle_file_upload: 파일 업로드 이벤트 처리 및 세션 상태 업데이트.
handle_reset: 세션 초기화 (모든 상태/히스토리 리셋).
handle_guide: 현재 상태 기반 워크플로 가이드 텍스트 생성.
handle_domain_change: 사용자 요청에 따라 도메인 변경.

리팩토링 Step 4e에서 agent.py CrewAgent로부터 추출됨.
"""

import logging
from typing import Any, Dict, Optional

from core.platform.session import SessionState, CrewSession, save_session_state
from core.platform.classifier import InputClassifier
from core.platform.utils import (
    build_guide_text, build_next_options, domain_display, error_response
)

logger = logging.getLogger(__name__)


async def handle_file_upload(session: CrewSession, project_id: str, event_data: Optional[Dict]
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
    # ★ Phase1: 파일 추가 시 분석/모델 상태 리셋
    state.analysis_completed = False
    state.math_model = None
    state.math_model_confirmed = False
    state.pending_param_inputs = None
    state.last_executed_skill = "FileReceivedSkill"

    # Save dataset version
    try:
        from core.version import create_dataset_version
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


async def handle_reset(session: CrewSession, project_id: str, message: str) -> Dict:
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


async def handle_guide(session: CrewSession, project_id: str, message: str) -> Dict:
    state = session.state
    guide_text = build_guide_text(state)
    return {
        "type": "guide",
        "text": guide_text,
        "data": None,
        "options": build_next_options(state)
    }


async def handle_domain_change(session: CrewSession, project_id: str, message: str) -> Dict:
    new_domain = InputClassifier.extract_domain_from_message(message)
    if new_domain:
        session.state.domain_override = new_domain
        session.state.detected_domain = new_domain
        session.state.domain_confidence = 1.0
        display = domain_display(new_domain)
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
