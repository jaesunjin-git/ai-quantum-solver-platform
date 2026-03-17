"""
core/job_router.py
──────────────────
비동기 솔버 Job API.

POST   /api/jobs/submit    : 솔버 실행 Job 생성 (즉시 반환)
GET    /api/jobs/{id}      : Job 상태 조회 (폴링)
GET    /api/jobs           : 프로젝트별 Job 목록
DELETE /api/jobs/{id}      : Job 취소
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
from core.models import JobDB, ProjectDB, UserDB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


# ── Schemas ──

class JobSubmitRequest(BaseModel):
    project_id: int
    solver_id: str
    solver_name: str = ""
    compare_group_id: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    solver_id: Optional[str] = None
    solver_name: Optional[str] = None
    progress: Optional[str] = None
    progress_pct: Optional[int] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ── Helper ──

def _job_to_response(job: JobDB, include_result: bool = False) -> JobStatusResponse:
    result = None
    if include_result and job.result_json:
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
        progress_pct=job.progress_pct,
        error=job.error,
        result=result,
        created_at=str(job.created_at) if job.created_at else None,
        started_at=str(job.started_at) if job.started_at else None,
        completed_at=str(job.completed_at) if job.completed_at else None,
    )


# ── Endpoints ──

@router.post("/submit", response_model=JobStatusResponse, status_code=202)
async def submit_job(
    body: JobSubmitRequest,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """솔버 실행 Job 생성. 즉시 job_id 반환."""

    # 중복 제출 방지: 같은 project + solver에 PENDING/RUNNING job이 있으면 차단
    existing = db.query(JobDB).filter(
        JobDB.project_id == body.project_id,
        JobDB.solver_id == body.solver_id,
        JobDB.status.in_(["PENDING", "RUNNING"]),
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"이미 실행 중인 Job이 있습니다 (job_id={existing.id})",
        )

    job = JobDB(
        project_id=body.project_id,
        solver_id=body.solver_id,
        solver_name=body.solver_name,
        compare_group_id=body.compare_group_id,
        backend="celery",
        status="PENDING",
        progress="대기 중",
        progress_pct=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Celery 태스크 큐잉 (Redis 미연결 시 graceful fallback)
    try:
        from core.celery_app import celery_app
        async_result = celery_app.send_task(
            "engine.tasks.run_solver_job",
            args=[job.id, body.project_id, body.solver_id, body.solver_name],
            queue="solver",
        )
        job.celery_task_id = async_result.id
        db.commit()
        logger.info(f"Job {job.id} queued to Celery (solver={body.solver_id}, task={async_result.id})")
    except Exception as e:
        # Redis 미실행 시 → 백그라운드 스레드 fallback (즉시 202 반환)
        logger.warning(f"Celery unavailable ({e}), running job in background thread")
        job.status = "RUNNING"
        job.started_at = datetime.datetime.now(datetime.timezone.utc)
        job.progress = "솔버 실행 준비 중"
        job.progress_pct = 5
        db.commit()

        import threading

        def _run_in_background(jid, pid, sid, sname):
            try:
                from engine.tasks import _run_solver_sync
                _run_solver_sync(jid, pid, sid, sname)
            except Exception as run_err:
                logger.error(f"Background job {jid} failed: {run_err}")
                from core.database import SessionLocal
                _db = SessionLocal()
                try:
                    from core.models import JobDB as _JDB
                    _j = _db.query(_JDB).filter(_JDB.id == jid).first()
                    if _j and _j.status not in ("COMPLETE", "CANCELLED"):
                        _j.status = "FAILED"
                        _j.error = str(run_err)
                        _j.completed_at = datetime.datetime.now(datetime.timezone.utc)
                        _j.progress = "실패"
                        _db.commit()
                except Exception:
                    _db.rollback()
                finally:
                    _db.close()

        t = threading.Thread(
            target=_run_in_background,
            args=(job.id, body.project_id, body.solver_id, body.solver_name),
            daemon=True,
        )
        t.start()
        logger.info(f"Job {job.id} started in background thread")

    return _job_to_response(job)


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
    return _job_to_response(job, include_result=True)


@router.delete("/{job_id}", response_model=JobStatusResponse)
async def cancel_job(
    job_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Job 취소. PENDING/RUNNING 상태만 취소 가능."""
    job = db.query(JobDB).filter(JobDB.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 이미 터미널 상태면 409
    if job.status in ("COMPLETE", "FAILED", "CANCELLED"):
        raise HTTPException(
            status_code=409,
            detail=f"Job already in terminal state: {job.status}",
        )

    # 권한 체크: 프로젝트 소유자 또는 admin만 취소 가능
    project = db.query(ProjectDB).filter(ProjectDB.id == job.project_id).first()
    owner_name = current_user.display_name or current_user.username
    if current_user.role != "admin" and project and project.owner != owner_name:
        raise HTTPException(status_code=403, detail="이 Job을 취소할 권한이 없습니다.")

    # Celery 태스크 취소 시도 (best-effort)
    if job.celery_task_id:
        try:
            from core.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
            logger.info(f"Celery task {job.celery_task_id} revoked for job {job_id}")
        except Exception as e:
            logger.warning(f"Celery revoke failed for job {job_id}: {e}")

    job.status = "CANCELLED"
    job.completed_at = datetime.datetime.now(datetime.timezone.utc)
    job.progress = "사용자 취소"
    db.commit()

    return _job_to_response(job)


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
    return [_job_to_response(j) for j in jobs]
