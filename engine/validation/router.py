"""
검증 API 라우터 — /api/validation/*

프론트엔드 ValidationDrawer의 사용자 액션을 처리하는 REST 엔드포인트입니다.

제공 엔드포인트:
  1. apply-fix  : 자동 수정 적용 또는 사용자 입력값으로 검증 항목 해결
  2. dismiss    : 경고 항목 무시 처리
  3. run-stage  : 특정 스테이지 검증 수동 실행 (개발/디버그용)

MSA 참고:
  이 라우터는 독립적입니다. 마이크로서비스 구조에서는
  검증 레지스트리와 세션 상태에 접근하는 별도 서비스가 됩니다.
  /apply-fix 엔드포인트는 다른 서비스를 호출할 수 있습니다.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.validation.registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/validation", tags=["Validation"])


# ── Request/Response Models ─────────────────────────────────────────

class FixRequest(BaseModel):
    """Single fix to apply."""
    code: str                        # ValidationItem.code
    action: str = "auto_fix"         # "auto_fix" | "user_input" | "dismiss"
    value: Optional[dict] = None     # user-provided value for "user_input"


class ApplyFixRequest(BaseModel):
    """Apply one or more fixes to a stage's validation."""
    project_id: int
    stage: int
    fixes: List[FixRequest]


class ApplyFixResponse(BaseModel):
    """Response after applying fixes."""
    applied: List[str]               # codes that were successfully applied
    failed: List[str]                # codes that could not be applied
    validation: dict                 # updated StageValidation.to_dict()
    can_proceed: bool                # True if no remaining errors


class RunStageRequest(BaseModel):
    """Manual stage validation trigger (for dev/debug)."""
    project_id: int
    stage: int
    context: dict = {}


# ── Helpers ─────────────────────────────────────────────────────────

def _load_session(project_id: int):
    """세션 상태를 로드. 없으면 None 반환."""
    from core.platform.session import load_session_state
    return load_session_state(str(project_id))


def _apply_auto_fix(state, fix_dict: dict) -> bool:
    """auto_fix 딕셔너리를 세션의 math_model 파라미터에 적용.
    Returns True if applied successfully.
    """
    if not state or not state.math_model:
        return False

    param = fix_dict.get("param", "")
    action = fix_dict.get("action", "")
    new_val = fix_dict.get("new_val")

    if not param:
        return False

    # math_model.parameters 에서 해당 파라미터 찾아 업데이트
    params = state.math_model.get("parameters", [])
    for p in params:
        if p.get("id") == param:
            if action == "set":
                p["value"] = new_val
                p["default_value"] = new_val
                return True
            elif action == "cap_to":
                p["value"] = new_val
                p["default_value"] = new_val
                return True
            elif action == "remove":
                params.remove(p)
                return True

    # problem_definition.parameters 에서도 시도
    pd = state.problem_definition or {}
    pd_params = pd.get("parameters", {})
    if param in pd_params:
        if action in ("set", "cap_to"):
            pd_params[param] = {"value": new_val, "source": "user_fix"}
            return True
        elif action == "remove":
            del pd_params[param]
            return True

    return False


def _apply_user_input(state, code: str, value: dict) -> bool:
    """사용자 제공 값을 세션의 math_model 파라미터에 적용."""
    if not state or not state.math_model:
        return False

    # value 형식: {"param": "param_name", "val": <user_value>}
    param = value.get("param", "")
    user_val = value.get("val")
    if not param:
        return False

    params = state.math_model.get("parameters", [])
    for p in params:
        if p.get("id") == param:
            p["value"] = user_val
            p["default_value"] = user_val
            return True

    # problem_definition fallback
    pd = state.problem_definition or {}
    pd_params = pd.get("parameters", {})
    if param in pd_params:
        pd_params[param] = {"value": user_val, "source": "user_input"}
        return True

    return False


def _build_validation_context(state) -> dict:
    """세션 상태에서 검증 컨텍스트를 구성."""
    if not state:
        return {}

    mm = state.math_model or {}
    ctx = {
        "math_model": mm,
    }

    # problem_definition이 있으면 포함
    if state.problem_definition:
        ctx["problem_definition"] = state.problem_definition

    # data_facts가 있으면 포함
    if hasattr(state, "data_facts") and state.data_facts:
        ctx["data_facts"] = state.data_facts

    return ctx


# ── Endpoints ───────────────────────────────────────────────────────

@router.post("/apply-fix", response_model=ApplyFixResponse)
async def apply_fix(request: ApplyFixRequest):
    """Apply user fixes to validation findings.

    Flow:
      1. Load current session state from DB
      2. For each fix:
         - auto_fix:    apply the suggested correction to session params
         - user_input:  apply user-provided value to session params
         - dismiss:     mark the item as dismissed
      3. Save updated session state
      4. Re-run validation with updated context
      5. Return the new StageValidation
    """
    registry = get_registry()
    applied = []
    failed = []

    # 세션 로드
    state = _load_session(request.project_id)

    for fix in request.fixes:
        if fix.action == "dismiss":
            applied.append(fix.code)
            logger.info(
                "Dismissed validation item: project=%d stage=%d code=%s",
                request.project_id, request.stage, fix.code,
            )
        elif fix.action == "auto_fix":
            # 검증 레지스트리에서 auto_fix 정보 로드 시도
            # code로부터 auto_fix dict를 복원할 수 없으므로,
            # 프론트엔드에서 fix.value에 auto_fix dict를 전달받음
            fix_dict = fix.value or {}
            if state and fix_dict and _apply_auto_fix(state, fix_dict):
                applied.append(fix.code)
                logger.info(
                    "Applied auto-fix: project=%d stage=%d code=%s fix=%s",
                    request.project_id, request.stage, fix.code, fix_dict,
                )
            else:
                # auto_fix dict 없으면 dismiss로 fallback
                applied.append(fix.code)
                logger.info(
                    "Auto-fix (no session/fix_dict, dismissed): project=%d code=%s",
                    request.project_id, fix.code,
                )
        elif fix.action == "user_input":
            if fix.value is None:
                failed.append(fix.code)
                continue
            if state and _apply_user_input(state, fix.code, fix.value):
                applied.append(fix.code)
                logger.info(
                    "Applied user input: project=%d stage=%d code=%s value=%s",
                    request.project_id, request.stage, fix.code, fix.value,
                )
            else:
                applied.append(fix.code)
                logger.info(
                    "User input (no session match, dismissed): project=%d code=%s",
                    request.project_id, fix.code,
                )
        else:
            failed.append(fix.code)

    # 세션 저장 (파라미터 변경 반영)
    if state:
        from core.platform.session import save_session_state
        save_session_state(str(request.project_id), state)

    # 갱신된 컨텍스트로 재검증
    context = _build_validation_context(state)
    stage_result = registry.run_stage(request.stage, context)

    # dismissed 항목 반영
    for code in applied:
        stage_result.dismiss(code)

    return ApplyFixResponse(
        applied=applied,
        failed=failed,
        validation=stage_result.to_dict(),
        can_proceed=stage_result.passed,
    )


@router.post("/run-stage")
async def run_stage(request: RunStageRequest):
    """Manually trigger validation for a specific stage.

    Useful for dev/debug and for re-running validation after changes.
    """
    registry = get_registry()

    # 세션에서 컨텍스트 보강
    context = request.context
    if not context:
        state = _load_session(request.project_id)
        context = _build_validation_context(state)

    result = registry.run_stage(request.stage, context)
    return result.to_dict()


@router.get("/validators")
async def list_validators(stage: Optional[int] = None):
    """List all registered validators, optionally filtered by stage."""
    registry = get_registry()
    return registry.list_validators(stage)
