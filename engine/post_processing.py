"""
engine/post_processing.py
─────────────────────────
솔버 실행 후처리 공통 헬퍼.

/api/solve (동기)와 /api/jobs/submit (비동기) 양쪽에서 호출하여
RunResult 생성, SessionState 업데이트, ChatHistory 카드 저장을 수행한다.

각 단계는 독립적 try/except + rollback으로 보호하여
하나가 실패해도 나머지는 정상 수행된다.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def post_process_solve_result(
    project_id: int,
    solver_id: str,
    solver_name: str,
    summary: Optional[Dict[str, Any]],
    status: str = "UNKNOWN",
    objective_value: Optional[float] = None,
    db=None,
    *,
    is_compare: bool = False,
) -> Optional[int]:
    """
    솔버 실행 후처리 공통 헬퍼.

    Returns:
        run_result_id (int | None): 생성된 RunResult의 ID. 실패 시 None.

    Steps:
        1. RunResult 생성 (항상)
        2. SessionState 업데이트 (compare=False일 때만)
        3. ChatHistory 카드 저장 (compare=False일 때만)
    """
    run_result_id = None
    timing = (summary or {}).get("timing", {})

    # ── 1. RunResult 생성 (항상) ──
    try:
        from core.platform.session import load_session_state
        state = load_session_state(str(project_id))
        model_version_id = getattr(state, "current_model_version_id", None) if state else None
        domain_type = getattr(state, "detected_domain", None) if state else None

        from core.version import create_run_result
        run_row = create_run_result(
            project_id=project_id,
            model_version_id=model_version_id,
            domain_type=domain_type,
            solver_id=solver_id,
            solver_name=solver_name,
            status=status,
            objective_value=objective_value,
            result_json=summary,
            compile_time_sec=timing.get("compile_sec"),
            execute_time_sec=timing.get("execute_sec"),
        )
        run_result_id = run_row.id
        logger.info(f"RunResult created: id={run_result_id}, project={project_id}")
    except Exception as e:
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(f"RunResult 생성 실패 (project={project_id}): {e}")

    # compare 모드: session/chat 스킵
    if is_compare:
        return run_result_id

    # ── 2. SessionState 업데이트 ──
    try:
        from core.platform.session import load_session_state, save_session_state
        state = load_session_state(str(project_id))
        if state:
            state.optimization_done = True
            state.solver_selected = solver_name or solver_id
            state.last_optimization_result = summary
            if run_result_id:
                state.current_run_id = run_result_id
            save_session_state(str(project_id), state)
            logger.info(f"SessionState updated: project={project_id}, solver={solver_name}")
    except Exception as e:
        logger.warning(f"SessionState 업데이트 실패 (project={project_id}): {e}")

    # ── 3. ChatHistory 카드 저장 ──
    if db:
        try:
            from core.models import ChatHistoryDB
            result_card = {
                "view_mode": "result",
                "solver_id": solver_id,
                "solver_name": solver_name,
                **(summary or {}),
            }
            chat_log = ChatHistoryDB(
                project_id=project_id,
                role="assistant",
                message_type="card",
                message_text=f"{solver_name} 최적화 완료. 결과를 확인하세요.",
                card_json=json.dumps(result_card, ensure_ascii=False, default=str),
            )
            db.add(chat_log)
            db.commit()
            logger.info(f"ChatHistory card saved: project={project_id}")
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"ChatHistory 저장 실패 (project={project_id}): {e}")

    return run_result_id
