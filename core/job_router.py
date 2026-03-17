"""
core/job_router.py
──────────────────
비동기 솔버 Job API.

POST /api/jobs/submit    : 솔버 실행 Job 생성 (즉시 반환)
GET  /api/jobs/{id}      : Job 상태 조회 (폴링)
GET  /api/jobs           : 프로젝트별 Job 목록
"""
from __future__ import annotations

import json
import logging
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.auth import get_current_user
from core.models import JobDB, UserDB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


# ── Schemas ──

class JobSubmitRequest(BaseModel):
    project_id: int
    solver_id: str
    solver_name: str = ""


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    solver_id: Optional[str] = None
    solver_name: Optional[str] = None
    progress: Optional[str] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ── Endpoints ──

@router.post("/submit", response_model=JobStatusResponse, status_code=202)
async def submit_job(
    body: JobSubmitRequest,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """솔버 실행 Job 생성. 즉시 job_id 반환."""
    job = JobDB(
        project_id=body.project_id,
        solver_id=body.solver_id,
        solver_name=body.solver_name,
        backend="celery",
        status="PENDING",
        progress="대기 중",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Celery 태스크 큐잉 (Redis 미연결 시 graceful fallback)
    try:
        from core.celery_app import celery_app
        celery_app.send_task(
            "engine.tasks.run_solver_job",
            args=[job.id, body.project_id, body.solver_id, body.solver_name],
            queue="solver",
        )
        logger.info(f"Job {job.id} queued to Celery (solver={body.solver_id})")
    except Exception as e:
        # Redis 미실행 시 → 동기 fallback 실행
        logger.warning(f"Celery unavailable ({e}), running job synchronously")
        job.status = "RUNNING"
        job.started_at = datetime.datetime.now(datetime.timezone.utc)
        job.progress = "동기 실행 중 (Celery 미연결)"
        db.commit()

        try:
            from engine.tasks import _run_solver_sync
            result = _run_solver_sync(job.id, body.project_id, body.solver_id, body.solver_name)
            job.status = "COMPLETE" if result.get("success") else "FAILED"
            job.result_json = json.dumps(result, ensure_ascii=False, default=str)
            job.error = result.get("error")
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.progress = "완료"
        except Exception as run_err:
            job.status = "FAILED"
            job.error = str(run_err)
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.progress = "실패"
        db.commit()

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        solver_id=job.solver_id,
        solver_name=job.solver_name,
        progress=job.progress,
        created_at=str(job.created_at),
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Job 상태 조회 (프론트엔드 폴링용)."""
    job = db.query(JobDB).filter(JobDB.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if job.result_json:
        try:
            result = json.loads(job.result_json)
        except Exception:
            pass

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        solver_id=job.solver_id,
        solver_name=job.solver_name,
        progress=job.progress,
        error=job.error,
        result=result,
        created_at=str(job.created_at) if job.created_at else None,
        started_at=str(job.started_at) if job.started_at else None,
        completed_at=str(job.completed_at) if job.completed_at else None,
    )


@router.get("", response_model=list[JobStatusResponse])
async def list_jobs(
    project_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """프로젝트별 Job 목록."""
    jobs = (
        db.query(JobDB)
        .filter(JobDB.project_id == project_id)
        .order_by(JobDB.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        JobStatusResponse(
            job_id=j.id,
            status=j.status,
            solver_id=j.solver_id,
            solver_name=j.solver_name,
            progress=j.progress,
            error=j.error,
            created_at=str(j.created_at) if j.created_at else None,
            started_at=str(j.started_at) if j.started_at else None,
            completed_at=str(j.completed_at) if j.completed_at else None,
        )
        for j in jobs
    ]
