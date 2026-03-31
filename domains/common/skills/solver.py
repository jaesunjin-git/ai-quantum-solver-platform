from __future__ import annotations
"""
domains/common/skills/solver.py
─────────────────────────────
솔버 추천, 실행, 결과 표시 스킬.

skill_pre_decision: 수학 모델 기반 솔버 적합성 분석 및 추천.
build_solver_response: 솔버 추천 결과를 프론트엔드 응답 형식으로 변환.
skill_start_optimization: 선택된 솔버로 최적화 실행.
skill_show_solver: 이전 솔버 추천 결과 재표시.
skill_show_opt_result: 이전 최적화 결과 재표시.

리팩토링 Step 4d에서 agent.py CrewAgent로부터 추출됨.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from core.platform.session import SessionState, CrewSession, save_session_state
from core.platform.utils import build_next_options, error_response, domain_display

from engine.pre_decision import run_pre_decision_analysis

logger = logging.getLogger(__name__)


def build_solver_response(state: SessionState, result: Dict) -> Dict:
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


async def skill_pre_decision(session: CrewSession, project_id: str, message: str, params: Dict
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

    # ★ Priority 파싱: IntentClassifier fast_path → 키워드 fallback
    from core.platform.intent_classifier import get_intent_classifier, log_intent
    _ic = get_intent_classifier()
    _solver_intent = _ic.fast_path("solver", message)
    priority = "auto"
    if _solver_intent:
        log_intent(project_id, message, _solver_intent, skill_name="solver")
        _intent_to_priority = {
            "priority_accuracy": "accuracy",
            "priority_speed": "speed",
            "priority_cost": "cost",
        }
        priority = _intent_to_priority.get(_solver_intent.intent, "auto")

    if priority == "auto":
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ["정확도 우선", "accuracy", "정확도"]):
            priority = "accuracy"
        elif any(kw in msg_lower for kw in ["속도 우선", "speed", "속도", "빠른"]):
            priority = "speed"
        elif any(kw in msg_lower for kw in ["비용 우선", "cost", "비용", "저렴"]):
            priority = "cost"

    # ★ 변경 2: priority가 auto가 아니면(사용자가 우선순위 변경 버튼 클릭) 캐시 무시
    if state.last_pre_decision_result and priority == "auto":
        return build_solver_response(state, state.last_pre_decision_result)

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

        return build_solver_response(state, result)

    except Exception as e:
        logger.error(f"[{project_id}] Pre-decision error: {e}", exc_info=True)
        return {
            "type": "error",
            "text": f"솔버 추천 중 오류가 발생했습니다: {str(e)}",
            "data": None,
            "options": [],
        }


async def skill_start_optimization(session: CrewSession, project_id: str, message: str, params: Dict
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


async def skill_show_solver(session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    state = session.state
    if state.last_pre_decision_result:
        return build_solver_response(state, state.last_pre_decision_result)
    # 데이터 없어도 target_tab 포함 — 프론트엔드에 이미 캐시된 솔버 뷰로 전환
    return {
        "type": "warning",
        "text": "⚠️ 솔버 추천 결과가 없습니다.",
        "data": {"target_tab": "solver"},
        "options": [{"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"}],
    }


async def skill_show_opt_result(session: CrewSession, project_id: str, message: str, params: Dict
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
    # 데이터 없어도 target_tab 포함 — 프론트엔드에 이미 캐시된 결과 뷰로 전환
    return {
        "type": "warning",
        "text": "⚠️ 아직 최적화가 실행되지 않아 표시할 결과가 없습니다.",
        "data": {"target_tab": "result"},
        "options": [{"label": "🚀 최적화 실행", "action": "send", "message": "최적화 실행해줘"}],
    }
