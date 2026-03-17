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


def _update_job_progress(db, job_id: int, progress: str, progress_pct: int):
    """Job progress 업데이트 (단조 증가 보장)."""
    from core.models import JobDB
    job = db.query(JobDB).filter(JobDB.id == job_id).first()
    if not job or job.status == "CANCELLED":
        return
    # 단조 증가: 현재 값보다 큰 경우에만 업데이트
    if job.progress_pct is None or progress_pct > job.progress_pct:
        job.progress_pct = progress_pct
    job.progress = progress
    db.commit()


def _update_job_status_if_not_cancelled(db, job_id: int, new_status: str, **kwargs) -> bool:
    """CANCELLED 상태가 아닐 때만 상태 업데이트. 취소 race condition 방지."""
    from core.models import JobDB
    job = db.query(JobDB).filter(JobDB.id == job_id).first()
    if not job or job.status == "CANCELLED":
        logger.info(f"Job {job_id} already CANCELLED — skipping {new_status} update")
        return False
    job.status = new_status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.commit()
    return True


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
            if job.status == "CANCELLED":
                logger.info(f"Job {job_id} already CANCELLED — aborting")
                return {"success": False, "error": "Job cancelled"}
            job.status = "RUNNING"
            job.started_at = datetime.datetime.now(datetime.timezone.utc)
            job.progress = "파이프라인 시작"
            job.progress_pct = 10
            db.commit()

        # 세션에서 math_model 로드
        state = load_session_state(str(project_id))
        if not state or not state.math_model:
            raise ValueError("math_model not found in session")

        math_model = state.math_model

        # time_limit 조회
        from engine.solver_registry import get_solver_time_limit
        time_limit = get_solver_time_limit(solver_id, db)

        # 컴파일 시작 progress
        _update_job_progress(db, job_id, "모델 컴파일 중", 20)

        # 파이프라인 실행
        import asyncio
        pipeline = SolverPipeline()

        # 시간 기반 progress 업데이트 (별도 스레드)
        import threading
        _progress_stop = threading.Event()

        def _progress_ticker():
            """5초마다 progress를 점진적으로 올림 (20→90 범위)"""
            from core.database import SessionLocal as _SL
            pct = 20
            while not _progress_stop.is_set():
                _progress_stop.wait(5)
                if _progress_stop.is_set():
                    break
                pct = min(pct + 3, 90)  # 최대 90%까지 (100%는 완료 시)
                try:
                    _db2 = _SL()
                    _update_job_progress(_db2, job_id, "솔버 파이프라인 실행 중", pct)
                    _db2.close()
                except Exception:
                    pass

        ticker = threading.Thread(target=_progress_ticker, daemon=True)
        ticker.start()

        coro = pipeline.run(
            math_model=math_model,
            solver_id=solver_id,
            project_id=str(project_id),
            solver_name=solver_name,
            time_limit_sec=time_limit,
        )
        # 이미 이벤트 루프가 실행 중이면 (FastAPI sync fallback) nest_asyncio 또는 to_thread 사용
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 이벤트 루프가 이미 실행 중 — 별도 스레드에서 새 루프 생성
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, coro).result()
        else:
            result = asyncio.run(coro)

        # progress ticker 중지
        _progress_stop.set()

        summary = result.summary if result.success else {}
        output = {
            "success": result.success,
            "phase": result.phase,
            "solver_id": result.solver_id,
            "solver_name": result.solver_name,
            "error": result.error,
            "summary": summary,
        }

        # 최종 상태 업데이트 (취소 가드 적용)
        final_status = "COMPLETE" if result.success else "FAILED"
        final_pct = 100 if result.success else None  # FAILED는 마지막 pct 유지
        updated = _update_job_status_if_not_cancelled(
            db, job_id, final_status,
            result_json=json.dumps(output, ensure_ascii=False, default=str),
            error=result.error,
            completed_at=datetime.datetime.now(datetime.timezone.utc),
            progress="완료" if result.success else "실패",
            progress_pct=final_pct if final_pct else None,
        )

        # CANCELLED면 후처리 스킵
        if not updated:
            logger.info(f"Job {job_id} was CANCELLED — skipping post-processing")
            return output

        # 후처리 (RunResult + SessionState + ChatHistory)
        if result.success:
            try:
                is_compare = False
                if job:
                    db.refresh(job)
                    is_compare = bool(job.compare_group_id)

                from engine.post_processing import post_process_solve_result
                post_process_solve_result(
                    project_id=int(project_id),
                    solver_id=solver_id,
                    solver_name=solver_name,
                    summary=summary,
                    status=result.execute_result.status if result.execute_result else "UNKNOWN",
                    objective_value=result.execute_result.objective_value if result.execute_result else None,
                    db=db,
                    is_compare=is_compare,
                )
            except Exception as pp_err:
                logger.warning(f"Post-processing failed for job {job_id}: {pp_err}")

        return output
    except Exception as e:
        # progress ticker 중지 (예외 경로)
        try:
            _progress_stop.set()
        except Exception:
            pass
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        if db:
            try:
                _update_job_status_if_not_cancelled(
                    db, job_id, "FAILED",
                    error=str(e),
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    progress="실패",
                )
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
