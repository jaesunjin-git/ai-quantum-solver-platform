"""
engine/feasibility — Column Feasibility Pipeline
=================================================
YAML 선언 기반 column feasibility 검증 파이프라인.

Column Gen이 column을 생성할 때:
  1. 기본 계산 (span, wait, cost)
  2. FeasibilityPipeline.run(column, params)  ← YAML 기반 검증
  3. _check_domain_specific(column)           ← 복잡 로직 hook
  4. _finalize_column(column)                 ← 도메인 보정

Pruning(beam search 조기 가지치기)과 분리:
  - Pruning: 근사적, 틀려도 됨, 성능 최적화 → 코드 레벨 유지
  - Validation: 확정적, 틀리면 안 됨 → 이 파이프라인

파라미터 참조 규칙:
  - `_param` 접미사: config/params에서 값 조회 (limit_param: max_idle_time)
  - 접미사 없음: 직접 값 사용 (limit: 360)
  - 둘 다 있으면 _param 우선, 직접 값은 fallback
"""

from engine.feasibility.base import (
    FeasibilityCheck,
    CheckResult,
    FeasibilityCheckRegistry,
    FeasibilityPipeline,
)

__all__ = [
    "FeasibilityCheck",
    "CheckResult",
    "FeasibilityCheckRegistry",
    "FeasibilityPipeline",
]
