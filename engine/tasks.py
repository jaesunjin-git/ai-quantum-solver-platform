"""
engine/tasks.py
───────────────
Celery 비동기 솔버 태스크.

Celery Worker에서 실행됩니다:
    celery -A core.celery_app worker --loglevel=info --pool=solo -Q solver
"""
from __future__ import annotations

import json
import logging
import datetime

logger = logging.getLogger(__name__)


def _run_solver_sync(
    job_id: int,
    project_id: int,
    solver_id: str,
    solver_name: str,
) -> dict:
    """동기 솔버 실행 (Celery 태스크 또는 fallback에서 호출)."""
    from core.database import SessionLocal
    from core.models import JobDB
    from core.platform.session import load_session_state
    from engine.solver_pipeline import SolverPipeline

    db = SessionLocal()
    try:
        job = db.query(JobDB).filter(JobDB.id == job_id).first()
        if job:
            job.status = "RUNNING"
            job.started_at = datetime.datetime.now(datetime.timezone.utc)
            job.progress = "솔버 실행 중"
            db.commit()

        # 세션에서 math_model 로드
        state = load_session_state(str(project_id))
        if not state or not state.math_model:
            raise ValueError("math_model not found in session")

        math_model = state.math_model

        # time_limit 조회
        from engine.solver_registry import get_solver_time_limit
        time_limit = get_solver_time_limit(solver_id, db)

        # 파이프라인 실행
        import asyncio
        pipeline = SolverPipeline()
        result = asyncio.run(pipeline.run(
            math_model=math_model,
            solver_id=solver_id,
            project_id=str(project_id),
            solver_name=solver_name,
            time_limit_sec=time_limit,
        ))

        summary = result.summary if result.success else {}
        output = {
            "success": result.success,
            "phase": result.phase,
            "solver_id": result.solver_id,
            "solver_name": result.solver_name,
            "error": result.error,
            "summary": summary,
        }

        if job:
            job.status = "COMPLETE" if result.success else "FAILED"
            job.result_json = json.dumps(output, ensure_ascii=False, default=str)
            job.error = result.error
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.progress = "완료" if result.success else "실패"
            db.commit()

        return output
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        if db:
            try:
                job = db.query(JobDB).filter(JobDB.id == job_id).first()
                if job:
                    job.status = "FAILED"
                    job.error = str(e)
                    job.completed_at = datetime.datetime.now(datetime.timezone.utc)
                    job.progress = "실패"
                    db.commit()
            except Exception:
                db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


# Celery 태스크 등록 (celery_app import 시에만 등록)
try:
    from core.celery_app import celery_app

    @celery_app.task(name="engine.tasks.run_solver_job", queue="solver")
    def run_solver_job(job_id: int, project_id: int, solver_id: str, solver_name: str):
        """Celery에서 실행되는 비동기 솔버 태스크."""
        return _run_solver_sync(job_id, project_id, solver_id, solver_name)

except ImportError:
    logger.warning("Celery not available — async job execution disabled")
