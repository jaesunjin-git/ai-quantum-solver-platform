# ============================================================
# core/project_router.py — v1.0
# ============================================================
# 프로젝트 CRUD API
# - POST   /api/projects          : 프로젝트 생성
# - GET    /api/projects          : 프로젝트 목록 조회
# - DELETE /api/projects/{id}     : 프로젝트 삭제 (관련 데이터 포함)
# ============================================================

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List
from urllib.parse import unquote

from core.database import get_db
import core.models as models
import core.schemas as schemas

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.post("", response_model=schemas.ProjectResponse)
def create_project(project: schemas.ProjectCreate, db: Session = Depends(get_db)):
    new_project = models.ProjectDB(
        title=project.title,
        type=project.type,
        owner=project.owner,
        status="In Progress",
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project


@router.get("", response_model=List[schemas.ProjectResponse])
def get_projects(
    user: str = Query(..., description="User Name"),
    db: Session = Depends(get_db),
):
    decoded_user = unquote(user)
    return (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.owner == decoded_user)
        .order_by(models.ProjectDB.created_at.desc())
        .all()
    )

@router.patch("/{project_id}", response_model=schemas.ProjectResponse)
def update_project(
    project_id: int,
    body: schemas.ProjectUpdate,
    user: str = Query(..., description="User Name"),
    role: str = Query(default="user", description="User Role"),
    db: Session = Depends(get_db),
):
    decoded_user = unquote(user)

    project = (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    if role != "admin" and project.owner != decoded_user:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")

    project.title = body.title
    db.commit()
    db.refresh(project)
    return project

@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    user: str = Query(..., description="User Name"),
    role: str = Query(default="user", description="User Role"),
    db: Session = Depends(get_db),
):
    decoded_user = unquote(user)

    # 프로젝트 조회
    project = (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    # 권한 확인: admin은 모두 삭제 가능, 일반 유저는 자기 것만
    if role != "admin" and project.owner != decoded_user:
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")

    # 관련 데이터 삭제 (FK 의존성 순서)
    db.query(models.ChatHistoryDB).filter(
        models.ChatHistoryDB.project_id == project_id
    ).delete()
    db.query(models.JobDB).filter(
        models.JobDB.project_id == project_id
    ).delete()
    db.query(models.SessionStateDB).filter(
        models.SessionStateDB.project_id == project_id
    ).delete()

    # 프로젝트 삭제
    project_title = project.title
    db.delete(project)
    db.commit()

    return {
        "message": f"프로젝트 '{project_title}'이(가) 삭제되었습니다.",
        "deleted_id": project_id,
    }