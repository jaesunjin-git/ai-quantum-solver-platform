"""
engine/cache — Solver 입력 캐싱
================================
solver 변경 재실행 시 중간 산출물(Column Pool 등)을 재사용.

설계 원칙:
  - 캐싱 인터페이스는 problem type에 무관 (플랫폼 레벨)
  - 캐싱 내용물(payload)은 problem type별로 다름:
    - crew_scheduling: Column Pool + task mapping
    - material_optimization: MLIP 에너지 맵 + Compiled QUBO (향후)
  - solver_id는 캐시 키에 포함하지 않음 (solver가 달라도 같은 입력)
  - extra_constraints는 캐시에 포함하지 않음 (compile 시점에 재생성)
"""

from engine.cache.solver_input_cache import SolverInputCache, FileSolverInputCache

__all__ = ["SolverInputCache", "FileSolverInputCache"]
