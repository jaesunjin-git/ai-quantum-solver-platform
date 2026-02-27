"""솔버 설정 API — Admin 전용"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import logging

from .database import get_db
from . import models
from engine.solver_registry import SolverRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["Settings"])


# ── Pydantic 스키마 ──
class SolverSettingOut(BaseModel):
    solver_id: str
    solver_name: str
    provider: str
    category: str
    description: str
    enabled: bool
    has_api_key: bool


class SolverSettingUpdate(BaseModel):
    solver_id: str
    enabled: bool
    api_key: Optional[str] = None


class SolverSettingsBulkUpdate(BaseModel):
    solvers: List[SolverSettingUpdate]


# ── GET: 전체 솔버 목록 + 활성 상태 ──
@router.get("/solvers", response_model=List[SolverSettingOut])
def get_solver_settings(
    role: str = Query(default="user"),
    db: Session = Depends(get_db),
):
    """모든 솔버 목록과 활성화 상태를 반환"""
    all_solvers = SolverRegistry.get_all()
    db_settings = {
        s.solver_id: s
        for s in db.query(models.SolverSettingDB).all()
    }

    result = []
    for solver in all_solvers:
        sid = solver.get("id", "")
        db_row = db_settings.get(sid)
        result.append(SolverSettingOut(
            solver_id=sid,
            solver_name=solver.get("name", ""),
            provider=solver.get("provider", ""),
            category=solver.get("category", ""),
            description=solver.get("description", ""),
            enabled=db_row.enabled if db_row else False,
            has_api_key=bool(db_row.api_key) if db_row else False,
        ))
    return result


# ── PUT: 솔버 설정 일괄 저장 (Admin 전용) ──
@router.put("/solvers")
def update_solver_settings(
    body: SolverSettingsBulkUpdate,
    role: str = Query(default="user"),
    db: Session = Depends(get_db),
):
    """솔버 활성화 상태 및 API 키 일괄 저장 (Admin 전용)"""
    if role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    for item in body.solvers:
        row = db.query(models.SolverSettingDB).filter_by(
            solver_id=item.solver_id
        ).first()

        if row:
            row.enabled = item.enabled
            if item.api_key is not None:
                row.api_key = item.api_key
            row.updated_by = "admin"
        else:
            row = models.SolverSettingDB(
                solver_id=item.solver_id,
                enabled=item.enabled,
                api_key=item.api_key,
                updated_by="admin",
            )
            db.add(row)
    
    # 솔버 설정 변경 시 모든 세션의 캐시 무효화
    db.query(models.SessionStateDB).update(
        {models.SessionStateDB.last_pre_decision_result: None}
    )

    db.commit()
    logger.info(f"Solver settings updated: {len(body.solvers)} solvers")
    return {"status": "ok", "updated": len(body.solvers)}


# ── GET: 활성화된 솔버 ID 목록만 ──
@router.get("/solvers/enabled", response_model=List[str])
def get_enabled_solver_ids(db: Session = Depends(get_db)):
    """활성화된 솔버 ID 목록만 반환 (내부 호출용)"""
    rows = db.query(models.SolverSettingDB).filter_by(enabled=True).all()
    return [r.solver_id for r in rows]