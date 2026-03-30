"""
Solver 입력 캐싱 테스트
========================
SolverInputCache 인터페이스 + 파일 기반 구현 + 캐시 키 생성 검증.
"""

import os
import pytest
import tempfile
import shutil


class TestBuildCacheKey:
    """캐시 키 생성 검증"""

    def test_same_input_same_key(self):
        from engine.cache.solver_input_cache import build_cache_key
        k1 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        k2 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        assert k1 == k2

    def test_different_problem_type_different_key(self):
        from engine.cache.solver_input_cache import build_cache_key
        k1 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        k2 = build_cache_key("material_optimization", model_version_id=1, data_version_id=1)
        assert k1 != k2

    def test_different_model_version_different_key(self):
        from engine.cache.solver_input_cache import build_cache_key
        k1 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        k2 = build_cache_key("crew_scheduling", model_version_id=2, data_version_id=1)
        assert k1 != k2

    def test_different_params_different_key(self):
        from engine.cache.solver_input_cache import build_cache_key
        k1 = build_cache_key("crew_scheduling", params_hash="abc")
        k2 = build_cache_key("crew_scheduling", params_hash="def")
        assert k1 != k2

    def test_solver_id_not_in_key(self):
        """solver_id는 키에 포함하지 않음 — solver 변경 시 캐시 재사용"""
        from engine.cache.solver_input_cache import build_cache_key
        # solver_id가 인자에 없으므로 같은 입력 → 같은 키
        k1 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        k2 = build_cache_key("crew_scheduling", model_version_id=1, data_version_id=1)
        assert k1 == k2


class TestHashParams:
    """파라미터 해시 검증"""

    def test_same_params_same_hash(self):
        from engine.cache.solver_input_cache import hash_params
        h1 = hash_params({"max_driving_minutes": 360, "max_wait_minutes": 300})
        h2 = hash_params({"max_driving_minutes": 360, "max_wait_minutes": 300})
        assert h1 == h2

    def test_different_params_different_hash(self):
        from engine.cache.solver_input_cache import hash_params
        h1 = hash_params({"max_driving_minutes": 360})
        h2 = hash_params({"max_driving_minutes": 480})
        assert h1 != h2

    def test_solver_related_keys_excluded(self):
        """solver 관련 키는 해시에서 제외"""
        from engine.cache.solver_input_cache import hash_params
        h1 = hash_params({"max_driving_minutes": 360, "solver_id": "classical_cpu"})
        h2 = hash_params({"max_driving_minutes": 360, "solver_id": "dwave_hybrid_cqm"})
        assert h1 == h2

    def test_domain_key_excluded(self):
        from engine.cache.solver_input_cache import hash_params
        h1 = hash_params({"max_driving_minutes": 360, "_domain": "railway"})
        h2 = hash_params({"max_driving_minutes": 360, "_domain": "bus"})
        assert h1 == h2


class TestFileSolverInputCache:
    """파일 기반 캐시 구현 검증"""

    @pytest.fixture(autouse=True)
    def setup_temp_dir(self, tmp_path, monkeypatch):
        """테스트용 임시 uploads 디렉토리"""
        self.uploads_dir = tmp_path / "uploads"
        self.uploads_dir.mkdir()
        monkeypatch.chdir(tmp_path)

    def test_save_and_load(self):
        from engine.cache.solver_input_cache import FileSolverInputCache
        cache = FileSolverInputCache()

        payload = {"columns": [1, 2, 3], "params": {"a": 1}}
        assert cache.save("test_project", "key123", payload) is True
        assert cache.exists("test_project", "key123") is True

        loaded = cache.load("test_project", "key123")
        assert loaded is not None
        assert loaded["columns"] == [1, 2, 3]
        assert loaded["params"]["a"] == 1

    def test_load_nonexistent(self):
        from engine.cache.solver_input_cache import FileSolverInputCache
        cache = FileSolverInputCache()
        assert cache.load("test_project", "nonexistent") is None
        assert cache.exists("test_project", "nonexistent") is False

    def test_invalidate(self):
        from engine.cache.solver_input_cache import FileSolverInputCache
        cache = FileSolverInputCache()

        cache.save("test_project", "key1", {"data": 1})
        cache.save("test_project", "key2", {"data": 2})
        assert cache.exists("test_project", "key1")
        assert cache.exists("test_project", "key2")

        count = cache.invalidate("test_project")
        assert count >= 2
        assert not cache.exists("test_project", "key1")
        assert not cache.exists("test_project", "key2")

    def test_metadata(self):
        from engine.cache.solver_input_cache import FileSolverInputCache, CacheMetadata
        import time
        cache = FileSolverInputCache()

        meta = CacheMetadata(
            cache_key="key123",
            problem_type="crew_scheduling",
            created_at=time.time(),
            payload_type="sp_columns",
        )
        cache.save("test_project", "key123", {"data": 1}, meta)

        loaded_meta = cache.get_metadata("test_project", "key123")
        assert loaded_meta is not None
        assert loaded_meta.problem_type == "crew_scheduling"
        assert loaded_meta.payload_type == "sp_columns"

    def test_overwrite(self):
        """같은 키로 다시 저장하면 덮어씀"""
        from engine.cache.solver_input_cache import FileSolverInputCache
        cache = FileSolverInputCache()

        cache.save("test_project", "key1", {"version": 1})
        cache.save("test_project", "key1", {"version": 2})

        loaded = cache.load("test_project", "key1")
        assert loaded["version"] == 2
