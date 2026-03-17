# ============================================================
# core/project_router.py — v2.0
# ============================================================
# 프로젝트 CRUD API (JWT 인증 적용)
# - POST   /api/projects          : 프로젝트 생성
# - GET    /api/projects          : 프로젝트 목록 조회
# - PATCH  /api/projects/{id}     : 프로젝트 이름 변경
# - DELETE /api/projects/{id}     : 프로젝트 삭제 (관련 데이터 포함)
# ============================================================

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from core.database import get_db
from core.auth import get_current_user
from core.models import UserDB
import core.models as models
import core.schemas as schemas

logger = logging.getLogger(__name__)
UPLOAD_BASE = Path("uploads")

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.post("", response_model=schemas.ProjectResponse)
def create_project(
    project: schemas.ProjectCreate,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_project = models.ProjectDB(
        title=project.title,
        type=project.type,
        owner=current_user.display_name or current_user.username,
        status="In Progress",
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project


@router.get("", response_model=List[schemas.ProjectResponse])
def get_projects(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_name = current_user.display_name or current_user.username
    if current_user.role == "admin":
        return (
            db.query(models.ProjectDB)
            .order_by(models.ProjectDB.created_at.desc())
            .all()
        )
    return (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.owner == owner_name)
        .order_by(models.ProjectDB.created_at.desc())
        .all()
    )


@router.patch("/{project_id}", response_model=schemas.ProjectResponse)
def update_project(
    project_id: int,
    body: schemas.ProjectUpdate,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    owner_name = current_user.display_name or current_user.username
    if current_user.role != "admin" and project.owner != owner_name:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")

    project.title = body.title
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 프로젝트 조회
    project = (
        db.query(models.ProjectDB)
        .filter(models.ProjectDB.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")

    # 권한 확인: admin은 모두 삭제 가능, 일반 유저는 자기 것만
    owner_name = current_user.display_name or current_user.username
    if current_user.role != "admin" and project.owner != owner_name:
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")

    # 관련 데이터 삭제 (FK 의존성 순서: 자식 테이블 먼저)
    db.query(models.RunResultDB).filter(
        models.RunResultDB.project_id == project_id
    ).delete()
    db.query(models.ModelVersionDB).filter(
        models.ModelVersionDB.project_id == project_id
    ).delete()
    db.query(models.DatasetVersionDB).filter(
        models.DatasetVersionDB.project_id == project_id
    ).delete()
    db.query(models.IntentLogDB).filter(
        models.IntentLogDB.project_id == project_id
    ).delete()
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

    # uploads/{project_id}/ 디렉토리 정리
    project_upload_dir = UPLOAD_BASE / str(project_id)
    if project_upload_dir.exists():
        try:
            shutil.rmtree(project_upload_dir)
            logger.info(f"Cleaned up upload dir: {project_upload_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up upload dir {project_upload_dir}: {e}")

    return {
        "message": f"프로젝트 '{project_title}'이(가) 삭제되었습니다.",
        "deleted_id": project_id,
    }
