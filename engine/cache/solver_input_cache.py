"""
solver_input_cache.py — Solver 입력 캐싱 인터페이스 + 파일 기반 구현
===================================================================
solver 변경 재실행 시 중간 산출물을 재사용하여:
  - Column Generation 스킵 (30초 → 즉시)
  - 비교 실행 시 동일 입력 보장 (공정성)
  - 결과 일관성 보장

캐시 키: hash(problem_type + model_version + data_version)
캐시 값: problem type별 중간 산출물 (직렬화)

직렬화 대상은 problem type별로 다름:
  - crew_scheduling: column pool + task mapping + params + objective_type
  - material_optimization: MLIP 에너지 맵 + compiled QUBO (향후)
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── 캐시 메타데이터 ────────────────────────────────────────

@dataclass
class CacheMetadata:
    """캐시 항목의 메타 정보"""
    cache_key: str
    problem_type: str
    created_at: float           # timestamp
    payload_type: str           # "sp_columns", "qubo_model" 등
    size_bytes: int = 0
    load_time_sec: float = 0.0


# ── 캐시 인터페이스 (플랫폼 레벨) ──────────────────────────

class SolverInputCache(ABC):
    """problem type에 무관한 solver 입력 캐싱 인터페이스.

    각 problem type의 pipeline이:
      1. save()로 중간 산출물 저장
      2. load()로 캐시된 산출물 복원
      3. invalidate()로 무효화

    직렬화 대상은 problem type이 결정 — 이 인터페이스는 bytes만 다룸.
    """

    @abstractmethod
    def save(self, project_id: str, cache_key: str,
             payload: Any, metadata: Optional[CacheMetadata] = None) -> bool:
        """중간 산출물 저장. 성공 시 True."""
        ...

    @abstractmethod
    def load(self, project_id: str, cache_key: str) -> Optional[Any]:
        """캐시된 산출물 복원. 미존재 시 None."""
        ...

    @abstractmethod
    def exists(self, project_id: str, cache_key: str) -> bool:
        """캐시 존재 여부."""
        ...

    @abstractmethod
    def invalidate(self, project_id: str) -> int:
        """프로젝트의 모든 캐시 무효화. 삭제된 항목 수 반환."""
        ...

    @abstractmethod
    def get_metadata(self, project_id: str, cache_key: str) -> Optional[CacheMetadata]:
        """캐시 메타데이터 조회."""
        ...


# ── 캐시 키 생성 ───────────────────────────────────────────

def build_cache_key(
    problem_type: str,
    model_version_id: Any = None,
    data_version_id: Any = None,
    params_hash: str = None,
) -> str:
    """캐시 키 생성.

    solver_id는 포함하지 않음 — solver가 달라도 같은 입력.
    extra_constraints도 포함하지 않음 — compile 시점에 재생성.
    """
    parts = [
        f"pt={problem_type}",
        f"mv={model_version_id or 'none'}",
        f"dv={data_version_id or 'none'}",
    ]
    if params_hash:
        parts.append(f"ph={params_hash}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def hash_params(params: Dict) -> str:
    """파라미터 dict의 해시 (캐시 키용).
    solver 관련 파라미터는 제외 — solver 변경 시 캐시 재사용을 위해."""
    # solver 관련 키 제외
    exclude_keys = {"_domain", "_task_map", "solver_id", "solver_name", "time_limit_sec"}
    filtered = {k: v for k, v in sorted(params.items())
                if k not in exclude_keys and not callable(v)}
    raw = str(filtered)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── 파일 기반 캐시 구현 ────────────────────────────────────

class FileSolverInputCache(SolverInputCache):
    """프로젝트 디렉토리에 pickle 파일로 캐시 저장.

    저장 위치: uploads/{project_id}/cache/solver_input_{cache_key}.pkl
    서버 재시작에도 유지됨.
    """

    CACHE_DIR = "cache"
    PREFIX = "solver_input_"

    def _cache_path(self, project_id: str, cache_key: str) -> str:
        return os.path.join(
            "uploads", str(project_id), self.CACHE_DIR,
            f"{self.PREFIX}{cache_key}.pkl"
        )

    def _meta_path(self, project_id: str, cache_key: str) -> str:
        return os.path.join(
            "uploads", str(project_id), self.CACHE_DIR,
            f"{self.PREFIX}{cache_key}.meta"
        )

    def save(self, project_id: str, cache_key: str,
             payload: Any, metadata: Optional[CacheMetadata] = None) -> bool:
        path = self._cache_path(project_id, cache_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
            t0 = time.time()
            with open(path, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            elapsed = time.time() - t0
            size = os.path.getsize(path)

            # 메타데이터 저장
            if metadata:
                metadata.size_bytes = size
                meta_path = self._meta_path(project_id, cache_key)
                with open(meta_path, "wb") as f:
                    pickle.dump(metadata, f)

            logger.info(
                f"SolverInputCache: saved {path} "
                f"({size / 1024 / 1024:.1f}MB, {elapsed:.2f}s)"
            )
            return True
        except Exception as e:
            logger.warning(f"SolverInputCache save failed: {e}")
            return False

    def load(self, project_id: str, cache_key: str) -> Optional[Any]:
        path = self._cache_path(project_id, cache_key)
        if not os.path.exists(path):
            return None

        try:
            t0 = time.time()
            with open(path, "rb") as f:
                payload = pickle.load(f)
            elapsed = time.time() - t0
            size = os.path.getsize(path)

            logger.info(
                f"SolverInputCache: loaded {path} "
                f"({size / 1024 / 1024:.1f}MB, {elapsed:.2f}s)"
            )
            return payload
        except Exception as e:
            logger.warning(f"SolverInputCache load failed: {e}")
            return None

    def exists(self, project_id: str, cache_key: str) -> bool:
        return os.path.exists(self._cache_path(project_id, cache_key))

    def invalidate(self, project_id: str) -> int:
        cache_dir = os.path.join("uploads", str(project_id), self.CACHE_DIR)
        if not os.path.isdir(cache_dir):
            return 0

        count = 0
        for fname in os.listdir(cache_dir):
            if fname.startswith(self.PREFIX):
                try:
                    os.remove(os.path.join(cache_dir, fname))
                    count += 1
                except OSError:
                    pass

        if count > 0:
            logger.info(f"SolverInputCache: invalidated {count} entries for project {project_id}")
        return count

    def get_metadata(self, project_id: str, cache_key: str) -> Optional[CacheMetadata]:
        meta_path = self._meta_path(project_id, cache_key)
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
