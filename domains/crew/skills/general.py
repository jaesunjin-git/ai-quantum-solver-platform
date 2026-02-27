from __future__ import annotations
"""
domains/crew/skills/general.py
──────────────────────────────
일반 대화 및 질의응답 스킬.

skill_answer: 사용자 질문에 대해 세션 컨텍스트 기반으로 LLM 답변 생성.
skill_general: 범용 대화 처리 (분류 불가 메시지 포함).
skill_ask_for_data: 데이터 업로드 요청 안내.

리팩토링 Step 4b에서 agent.py CrewAgent로부터 추출됨.
"""

import logging
import asyncio
import json
import re
from typing import Any, Dict, Optional

from domains.crew.session import SessionState, CrewSession, save_session_state
from domains.crew.utils import (build_next_options, clean_report, error_response, extract_text_from_llm)
from utils.prompt_loader import load_yaml_prompt

logger = logging.getLogger(__name__)


async def skill_general(model, session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    # params에 message가 있으면 그대로 사용
    reply = params.get("message", "")
    if reply:
        return {
            "type": "text",
            "text": clean_report(reply),
            "data": None,
            "options": build_next_options(session.state)
        }

     # LLM fallback
    if model:
        try:
            state = session.state

            # ── 풍부한 컨텍스트 구성 ──
            context_parts = [
                load_yaml_prompt("crew", "general_chat").get("system_with_context", "당신은 KQC 최적화 에이전트입니다."),
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
                model.generate_content, context
            )
            reply_text = response.text.strip()
            reply_text = extract_text_from_llm(reply_text)
            return {
                "type": "text",
                "text": reply_text,
                "data": None,
                "options": build_next_options(state)
            }
        except Exception as e:
            logger.error(f"General LLM error: {e}")

    return {
        "type": "text",
        "text": "요청을 처리하는 중 문제가 발생했습니다. 다시 시도해 주세요.",
        "data": None,
        "options": build_next_options(session.state)
    }


async def skill_ask_for_data(session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    question = params.get("question", "추가 데이터가 필요합니다.")
    return {
        "type": "text",
        "text": f"📋 {question}",
        "data": None,
        "options": [{"label": "📁 파일 업로드", "action": "upload"}],
    }


async def skill_answer(model, build_action_history_fn, session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    # LLM이 직접 답변을 제공한 경우 (answer 또는 message 키)
    answer = params.get("answer", "") or params.get("message", "")
    if answer and not answer.startswith("{"):
        return {
            "type": "text",
            "text": clean_report(answer),
            "data": None,
            "options": build_next_options(session.state)
        }

    # 답변이 없으면 데이터를 포함하여 LLM에게 직접 질문
    state = session.state
    query = params.get("query", "") or message

    if model:
        try:
            context_parts = [
                load_yaml_prompt("crew", "general_chat").get("system_without_context", "당신은 KQC 최적화 에이전트입니다."),
                "중요: 반드시 질문에 대한 답변 텍스트만 출력하세요.",
                "절대 스킬명(AnalyzeDataSkill 등)이나 JSON을 출력하지 마세요.",
                "절대 '~를 실행하겠습니다', '~를 수행합니다' 같은 안내를 하지 마세요.",
                "데이터나 분석 관련 질문이면 아래 데이터를 근거로 설명하고,",
                "일반적인 질문이면 친절하게 답변하세요.",
                "",
                f"[현재 상태] {state.context_string()}",
                "",
                build_action_history_fn(session),
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
                model.generate_content, context
            )
            reply_text = response.text.strip()
            reply_text = extract_text_from_llm(reply_text)
            return {
                "type": "text",
                "text": reply_text,
                "data": None,
                "options": build_next_options(state)
            }
        except Exception as e:
            logger.error(f"AnswerQuestion LLM error: {e}")

    return await skill_general(session, project_id, message, params)
