from __future__ import annotations
"""
domains/common/skills/analyze.py
──────────────────────────────
데이터 분석 관련 스킬.

skill_analyze: 업로드된 파일을 분석하여 도메인 감지, CSV 요약,
               LLM 기반 분석 리포트를 생성하고 프론트엔드에 전달.
skill_show_analysis: 이전 분석 결과를 다시 표시.

리팩토링 Step 4a에서 agent.py CrewAgent로부터 추출됨.
"""

from engine.file_service import analyze_csv_summary
from engine.gates.gate1_data_profile import run as run_gate1, to_text_summary as gate1_to_text

import logging
import re
from typing import Any, Dict, Optional

from core.platform.session import SessionState, CrewSession, save_session_state
from core.platform.utils import (
    build_facts_summary, clean_report, domain_display,
    build_next_options, error_response
)

from engine.file_service import extract_data_facts_async
from utils.prompt_builder import build_analysis_prompt
from core.platform.classifier import InputClassifier
import asyncio

logger = logging.getLogger(__name__)


async def skill_analyze(model, session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    state = session.state

    if not state.file_uploaded:
        return {
            "type": "warning",
            "text": "⚠️ 파일이 업로드되지 않았습니다. 먼저 스케줄 데이터 파일을 업로드해 주세요.",
            "data": None,
            "options": [{"label": "📁 파일 업로드", "action": "upload"}],
        }


    # 이미 분석 완료된 경우 캐시 반환 (재분석 요청이 아니면)
    if state.analysis_completed and state.last_analysis_report:
        from core.platform.intent_classifier import get_intent_classifier, log_intent
        _ic = get_intent_classifier()
        _intent = _ic.fast_path("analyze", message)
        if _intent:
            log_intent(project_id, message, _intent, skill_name="analyze")
        _is_reanalyze = (_intent and _intent.intent == "reanalyze")
        if not _is_reanalyze:
            # fast_path 미매칭 시 키워드 fallback
            _reanalyze_kw = ["다시", "재분석", "재 분석", "reanalyze"]
            _is_reanalyze = any(kw in message for kw in _reanalyze_kw)
        if _is_reanalyze:
            state.reset_from_analysis()
            save_session_state(project_id, state)
            # 아래 분석 로직으로 계속 진행
        else:
            domain = state.domain_override or state.detected_domain
            confidence = 1.0 if state.domain_override else state.domain_confidence
            display = domain_display(domain)
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
                        "primary": {"label": "📋 문제 정의 시작", "message": "문제 정의 시작"},
                        "secondary": {"label": " 다시 분석", "message": "다시 분석해줘"},
                    },
                },
                "options": [
                    {"label": "📋 문제 정의 시작", "action": "send", "message": "문제 정의 시작"},
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
        facts_summary = build_facts_summary(data_facts)

        # ★ Gate 1: 데이터 품질 프로파일링
        from engine.compiler.base import DataBinder
        binder = DataBinder(project_id)
        binder.load_files()
        data_profile = run_gate1(binder._dataframes)
        state.data_profile = data_profile
        profile_text = gate1_to_text(data_profile)
        logger.info(f"Gate1 completed: {len(data_profile.get('warnings', []))} warnings")

        prompt = build_analysis_prompt(
            csv_summary=csv_summary or "데이터 요약을 생성할 수 없습니다.",
            context=state.context_string(),
            detected_domain=domain,
            domain_confidence=confidence,
            data_facts=facts_summary,
            data_profile_text=profile_text,
        )
        response = await asyncio.to_thread(
            model.generate_content, prompt
        )
        report = response.text.strip()
        report = clean_report(report)

        state.analysis_completed = True
        state.last_analysis_report = report
        state.last_executed_skill = "AnalyzeDataSkill"

        display = domain_display(domain)
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
                    "primary": {"label": "📋 문제 정의 시작", "message": "문제 정의 시작"},
                    "secondary": {"label": "🔄 다시 분석", "message": "다시 분석해줘"},
                },
            },
            "options": [
                {"label": "📋 문제 정의 시작", "action": "send", "message": "문제 정의 시작"},
                {"label": "🔄 다시 분석", "action": "send", "message": "다시 분석해줘"},
                {"label": "🌐 도메인 변경", "action": "send", "message": "도메인 변경"},
            ],
        }

    except Exception as e:
        logger.error(f"AnalyzeDataSkill failed: {e}", exc_info=True)
        return error_response("분석 중 오류가 발생했습니다.", "분석 시작해줘")


async def skill_show_analysis(session: CrewSession, project_id: str, message: str, params: Dict
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
                    "primary": {"label": "📋 문제 정의 시작", "message": "문제 정의 시작"},
                    "secondary": {"label": "🔄 다시 분석", "message": "다시 분석해줘"},
                },
            },
            "options": [
                {"label": "📋 문제 정의 시작", "action": "send", "message": "문제 정의 시작"},
                {"label": "🔄 다시 분석", "action": "send", "message": "다시 분석해줘"},
            ],
        }
    return {
        "type": "warning",
        "text": "⚠️ 아직 분석 결과가 없습니다. 먼저 데이터 분석을 진행해 주세요.",
        "data": None,
        "options": [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}],
    }
