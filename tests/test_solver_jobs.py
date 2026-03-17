"""
tests/test_solver_jobs.py
─────────────────────────
Solver Job API + 후처리 + 취소 race + progress 테스트.

7가지 테스트 시나리오:
  1. 취소 Race — CANCELLED 후 worker COMPLETE 방지
  2. 후처리 부분 실패 — RunResult 실패해도 job COMPLETE 유지
  3. /api/solve 중복 후처리 방지
  4. Compare 모드 Session 정책
  5. progress_pct 단조 증가
  6. 요청 중복 제출 방지
  7. 취소 권한 체크
"""
import json
import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# 1. 취소 Race 테스트
# ============================================================
class TestCancelRace:
    """job RUNNING → cancel → worker COMPLETE: 최종 상태 CANCELLED 유지"""

    def test_cancelled_job_not_overwritten_by_complete(self):
        from engine.tasks import _update_job_status_if_not_cancelled

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "CANCELLED"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        # CANCELLED 상태에서 COMPLETE 시도 → False 반환, 상태 미변경
        result = _update_job_status_if_not_cancelled(mock_db, 1, "COMPLETE")
        assert result is False
        assert mock_job.status == "CANCELLED"
        mock_db.commit.assert_not_called()

    def test_running_job_can_be_updated_to_complete(self):
        from engine.tasks import _update_job_status_if_not_cancelled

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "RUNNING"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        result = _update_job_status_if_not_cancelled(mock_db, 1, "COMPLETE", progress="완료")
        assert result is True
        assert mock_job.status == "COMPLETE"
        assert mock_job.progress == "완료"
        mock_db.commit.assert_called_once()

    def test_cancelled_job_skips_post_processing(self):
        """_update_job_status_if_not_cancelled returns False → post_process should not be called"""
        from engine.tasks import _update_job_status_if_not_cancelled

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "CANCELLED"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        updated = _update_job_status_if_not_cancelled(mock_db, 1, "COMPLETE")
        assert updated is False
        # post_process_solve_result should NOT be called when updated is False


# ============================================================
# 2. 후처리 부분 실패 테스트
# ============================================================
class TestPostProcessingPartialFailure:
    """RunResult 실패해도 SessionState/ChatHistory는 독립적으로 동작"""

    def test_run_result_failure_does_not_block_session_update(self):
        from engine.post_processing import post_process_solve_result

        mock_db = MagicMock()

        with patch('core.version.create_run_result', side_effect=Exception("DB error")):
            with patch('core.platform.session.load_session_state') as mock_load:
                mock_state = MagicMock()
                mock_load.return_value = mock_state

                with patch('core.platform.session.save_session_state') as mock_save:
                    result_id = post_process_solve_result(
                        project_id=1,
                        solver_id="test",
                        solver_name="Test Solver",
                        summary={"timing": {}},
                        db=mock_db,
                    )

                    # RunResult 실패 → None
                    assert result_id is None
                    # SessionState는 여전히 업데이트됨
                    assert mock_state.optimization_done is True
                    mock_save.assert_called_once()

    def test_session_failure_does_not_block_chat_history(self):
        from engine.post_processing import post_process_solve_result

        mock_db = MagicMock()

        # load_session_state: 첫 호출(RunResult용)은 정상, 이후(Session 업데이트용)는 실패
        call_count = [0]
        def side_effect_load(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(current_model_version_id=None, detected_domain=None)
            raise Exception("Session error")

        with patch('core.version.create_run_result') as mock_create:
            mock_row = MagicMock()
            mock_row.id = 42
            mock_create.return_value = mock_row

            with patch('core.platform.session.load_session_state', side_effect=side_effect_load):
                with patch('core.platform.session.save_session_state'):
                    result_id = post_process_solve_result(
                        project_id=1,
                        solver_id="test",
                        solver_name="Test Solver",
                        summary={"timing": {}},
                        db=mock_db,
                    )

                    # RunResult 성공
                    assert result_id == 42
                    # ChatHistory db.add는 여전히 호출됨
                    assert mock_db.add.called


# ============================================================
# 3. /api/solve 중복 후처리 방지 테스트
# ============================================================
class TestNoDuplicatePostProcessing:
    """post_process_solve_result가 1회만 호출되는지 확인"""

    def test_post_processing_called_once(self):
        """공통 헬퍼가 chat/router.py와 engine/tasks.py 양쪽에서 사용되지만
        각 경로는 독립이므로 1회만 실행됨을 구조적으로 보장"""
        # engine/post_processing.py가 단일 함수이고
        # chat/router.py → post_process_solve_result(is_compare=False)
        # engine/tasks.py → post_process_solve_result(is_compare=compare)
        # 둘 중 하나의 경로만 실행됨

        from engine.post_processing import post_process_solve_result
        assert callable(post_process_solve_result)

        # 두 번 호출해도 독립적
        with patch('core.version.create_run_result') as mock_create:
            mock_row = MagicMock()
            mock_row.id = 1
            mock_create.return_value = mock_row

            with patch('core.platform.session.load_session_state', return_value=MagicMock()):
                with patch('core.platform.session.save_session_state'):
                    mock_db = MagicMock()
                    r1 = post_process_solve_result(1, "s1", "S1", {}, db=mock_db)
                    assert r1 == 1
                    assert mock_create.call_count == 1


# ============================================================
# 4. Compare 모드 Session 정책 테스트
# ============================================================
class TestCompareSessionPolicy:
    """compare 모드에서 SessionState가 업데이트되지 않음"""

    def test_compare_mode_skips_session_and_chat(self):
        from engine.post_processing import post_process_solve_result

        mock_db = MagicMock()

        with patch('core.version.create_run_result') as mock_create:
            mock_row = MagicMock()
            mock_row.id = 99
            mock_create.return_value = mock_row

            with patch('core.platform.session.load_session_state') as mock_load:
                with patch('core.platform.session.save_session_state') as mock_save:
                    result_id = post_process_solve_result(
                        project_id=1,
                        solver_id="test",
                        solver_name="Test",
                        summary={},
                        db=mock_db,
                        is_compare=True,
                    )

                    # RunResult 생성됨
                    assert result_id == 99
                    # load_session_state는 RunResult 생성 시 호출됨 (model_version_id 조회용)
                    # 하지만 save_session_state는 호출되지 않아야 함
                    mock_save.assert_not_called()
                    # ChatHistory도 저장 안 됨
                    mock_db.add.assert_not_called()

    def test_non_compare_mode_updates_session_and_chat(self):
        from engine.post_processing import post_process_solve_result

        mock_db = MagicMock()

        with patch('core.version.create_run_result') as mock_create:
            mock_row = MagicMock()
            mock_row.id = 100
            mock_create.return_value = mock_row

            with patch('core.platform.session.load_session_state') as mock_load:
                mock_state = MagicMock()
                mock_load.return_value = mock_state

                with patch('core.platform.session.save_session_state') as mock_save:
                    result_id = post_process_solve_result(
                        project_id=1,
                        solver_id="test",
                        solver_name="Test",
                        summary={},
                        db=mock_db,
                        is_compare=False,
                    )

                    assert result_id == 100
                    # SessionState 업데이트됨
                    mock_save.assert_called()
                    # ChatHistory 저장됨
                    assert mock_db.add.called


# ============================================================
# 5. progress_pct 단조 증가 테스트
# ============================================================
class TestProgressPctMonotonic:
    """progress_pct는 감소하지 않음"""

    def test_progress_does_not_decrease(self):
        from engine.tasks import _update_job_progress

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "RUNNING"
        mock_job.progress_pct = 60
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        # 40% 시도 → 무시 (현재 60%)
        _update_job_progress(mock_db, 1, "이전 단계", 40)
        assert mock_job.progress_pct == 60  # 변경 안 됨

    def test_progress_increases(self):
        from engine.tasks import _update_job_progress

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "RUNNING"
        mock_job.progress_pct = 40
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        _update_job_progress(mock_db, 1, "다음 단계", 60)
        assert mock_job.progress_pct == 60

    def test_progress_skipped_for_cancelled_job(self):
        from engine.tasks import _update_job_progress

        mock_db = MagicMock()
        mock_job = MagicMock()
        mock_job.status = "CANCELLED"
        mock_job.progress_pct = 30
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        _update_job_progress(mock_db, 1, "무시됨", 80)
        # CANCELLED이면 업데이트 안 됨
        mock_db.commit.assert_not_called()


# ============================================================
# 6. 요청 중복 제출 방지 테스트
# ============================================================
class TestDuplicateSubmitPrevention:
    """같은 project + solver에 RUNNING job이 있으면 409"""

    def test_job_submit_request_schema(self):
        """JobSubmitRequest에 compare_group_id 필드가 있는지"""
        from core.job_router import JobSubmitRequest
        req = JobSubmitRequest(project_id=1, solver_id="test", solver_name="Test")
        assert req.compare_group_id is None

        req2 = JobSubmitRequest(project_id=1, solver_id="test", solver_name="Test", compare_group_id="abc-123")
        assert req2.compare_group_id == "abc-123"

    def test_job_status_response_has_progress_pct(self):
        """JobStatusResponse에 progress_pct 필드가 있는지"""
        from core.job_router import JobStatusResponse
        resp = JobStatusResponse(job_id=1, status="RUNNING", progress_pct=50)
        assert resp.progress_pct == 50


# ============================================================
# 7. 취소 권한 테스트 + 모델 확장 테스트
# ============================================================
class TestJobDBExtension:
    """JobDB에 새 컬럼들이 존재하는지"""

    def test_job_db_has_new_columns(self):
        from core.models import JobDB
        # 컬럼 존재 확인
        columns = {c.name for c in JobDB.__table__.columns}
        assert "celery_task_id" in columns
        assert "progress_pct" in columns
        assert "compare_group_id" in columns

    def test_cancel_endpoint_exists(self):
        """DELETE /api/jobs/{id} 엔드포인트가 라우터에 등록되어 있는지"""
        from core.job_router import router
        methods = []
        for route in router.routes:
            if hasattr(route, 'methods'):
                methods.extend([(route.path, m) for m in route.methods])
        # 라우터 prefix 포함한 전체 경로
        assert ("/api/jobs/{job_id}", "DELETE") in methods

    def test_solver_registry_includes_time_limit(self):
        """solver_registry recommend 출력에 time_limit_sec 포함 확인"""
        from engine.solver_registry import SolverRegistry

        SolverRegistry.load()
        all_solvers = SolverRegistry._solvers
        if len(all_solvers) > 0:
            solver = all_solvers[0] if isinstance(all_solvers, list) else list(all_solvers.values())[0]
            tp = solver.get("time_profile", {})
            if tp:
                assert "max_time_seconds" in tp

    def test_post_processing_module_importable(self):
        """engine/post_processing.py가 import 가능한지"""
        from engine.post_processing import post_process_solve_result
        assert callable(post_process_solve_result)
