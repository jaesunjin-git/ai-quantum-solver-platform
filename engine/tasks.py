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
    strategy: str = "single",
    reuse_pool: bool = True,
) -> dict:
    """동기 솔버 실행 (Celery 태스크 또는 fallback에서 호출)."""
    from core.database import SessionLocal
    from core.models import JobDB
    from core.platform.session import load_session_state
    from engine.solver_pipeline import SolverPipeline

    import asyncio
    import threading

    db = SessionLocal()
    _progress_stop = threading.Event()  # 예외 경로에서도 접근 가능하도록 미리 생성

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
        pipeline = SolverPipeline()

        # 도메인 adapter 주입 (GR-1: domain_registry 경유, 하드코딩 없음)
        from engine.domain_registry import get_domain_adapter
        domain = getattr(state, "detected_domain", None) or "railway"
        adapter = get_domain_adapter(domain)
        if adapter:
            pipeline.set_domain_adapter(**adapter)
        else:
            logger.warning(f"Domain adapter '{domain}' not available — using generic base")

        # 시간 기반 progress 업데이트 (별도 스레드)
        def _progress_ticker():
            """5초마다 progress를 점진적으로 올림 (20→90 범위).
            자체 DB 세션 사용 (메인 스레드 세션과 격리).
            _progress_stop이 set되면 즉시 종료."""
            from core.database import SessionLocal as _SL
            pct = 20
            while not _progress_stop.is_set():
                _progress_stop.wait(5)
                if _progress_stop.is_set():
                    break
                pct = min(pct + 3, 90)
                _db2 = None
                try:
                    _db2 = _SL()
                    _update_job_progress(_db2, job_id, "솔버 파이프라인 실행 중", pct)
                except Exception:
                    if _db2:
                        try:
                            _db2.rollback()
                        except Exception:
                            pass
                finally:
                    if _db2:
                        try:
                            _db2.close()
                        except Exception:
                            pass

        ticker = threading.Thread(target=_progress_ticker, daemon=True)
        ticker.start()

        try:
            if strategy == "quantum_warmstart":
                coro = pipeline.run_hybrid(
                    math_model=math_model,
                    project_id=str(project_id),
                    solver_name=solver_name or "Hybrid (CQM → CP-SAT)",
                    time_limit_sec=time_limit,
                    reuse_pool=reuse_pool,
                )
            else:
                coro = pipeline.run(
                    math_model=math_model,
                    solver_id=solver_id,
                    project_id=str(project_id),
                    solver_name=solver_name,
                    time_limit_sec=time_limit,
                    reuse_pool=reuse_pool,
                )
            # 이미 이벤트 루프가 실행 중이면 별도 스레드에서 새 루프 생성
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(asyncio.run, coro).result()
            else:
                result = asyncio.run(coro)
        finally:
            # 파이프라인 완료/실패 관계없이 progress 스레드 즉시 중지
            # → 완료된 Job의 status를 덮어쓰는 race condition 방지
            _progress_stop.set()
            ticker.join(timeout=3)  # 스레드 종료 대기 (최대 3초)

        summary = result.summary or {}
        infeasibility_info = summary.get("infeasibility_info")
        output = {
            "success": result.success,
            "phase": result.phase,
            "solver_id": result.solver_id,
            "solver_name": result.solver_name,
            "error": result.error,
            "summary": summary,
            "infeasibility_info": infeasibility_info,
        }

        # 최종 상태 업데이트 (취소 가드 적용)
        final_status = "COMPLETE" if result.success else "FAILED"
        final_pct = 100 if result.success else None
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
        _progress_stop.set()  # 예외 시에도 반드시 progress 스레드 중지
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        try:
            _update_job_status_if_not_cancelled(
                db, job_id, "FAILED",
                error=str(e),
                completed_at=datetime.datetime.now(datetime.timezone.utc),
                progress="실패",
            )
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return {"success": False, "error": str(e)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# Celery 태스크 등록 (celery_app import 시에만 등록)
try:
    from core.celery_app import celery_app

    @celery_app.task(name="engine.tasks.run_solver_job", queue="solver")
    def run_solver_job(job_id: int, project_id: int, solver_id: str, solver_name: str):
        """Celery에서 실행되는 비동기 솔버 태스크."""
        return _run_solver_sync(job_id, project_id, solver_id, solver_name)

except ImportError:
    logger.warning("Celery not available — async job execution disabled")
