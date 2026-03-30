"""
engine/constraints — SP Side Constraint Pipeline
=================================================
YAML 선언 기반 SP 모델 제약 추가 파이프라인.

SP builder가 모델을 구축한 후:
  1. SideConstraintPipeline.build_all(columns, params)
  2. 각 handler가 columns 전체를 보고 SPConstraint 생성
  3. 생성된 SPConstraint를 SP problem의 extra_constraints에 추가

FeasibilityCheck(column 1개 판정)와의 차이:
  - SideConstraintHandler는 column 전체 목록을 받아 SPConstraint를 생성
  - cardinality: "조건 만족 column이 N개 이상 선택"
  - aggregate_avg: "선택된 column의 필드 평균이 limit 이하"
"""

from engine.constraints.base import (
    SideConstraintHandler,
    ConstraintResult,
    SideConstraintRegistry,
    SideConstraintPipeline,
)

__all__ = [
    "SideConstraintHandler",
    "ConstraintResult",
    "SideConstraintRegistry",
    "SideConstraintPipeline",
]
