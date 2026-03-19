"""
presolve_models.py ──────────────────────────────────────────
Presolve Feasibility Probing 데이터 모델.

Stage 5에서 솔버 실행 전 feasibility를 사전 판정하기 위한
모든 입출력 데이터 구조를 정의한다.

핵심 원칙:
  - Presolve INFEASIBLE = strong signal (실행 차단 가능)
  - Presolve FEASIBLE ≠ guaranteed feasible (보수적 근사)
  - 변환 손실(dropped/relaxed)은 반드시 리포트
  - 결과는 "최소 충돌 후보 집합"이며 보장된 minimal이 아닐 수 있음
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Status Enum ──────────────────────────────────────────────

class PresolveStatus(str, Enum):
    """Presolve 판정 결과"""
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    TIMEOUT = "TIMEOUT"
    TRIVIAL_INFEASIBLE = "TRIVIAL_INFEASIBLE"
    SKIPPED = "SKIPPED"


class GuaranteeLevel(str, Enum):
    """충돌 집합의 최소성 보장 수준"""
    VERIFIED_MINIMAL = "verified_minimal"   # 재검증 통과
    MINIMAL = "minimal"                     # QuickXPlain 완료 (시간 내)
    APPROXIMATE = "approximate"             # 시간/횟수 초과로 근사
    NONE = "none"                           # 탐색 미실행


class DroppedImpactLevel(str, Enum):
    """변환 시 생략된 제약의 영향도"""
    LOW = "low"       # 생략된 제약이 soft이고 소수
    MEDIUM = "medium"  # 생략 5개 이상 또는 일부 중요 제약 포함
    HIGH = "high"     # hard/capacity/coverage 제약 생략


class FidelityDecision(str, Enum):
    """Fidelity 기반 실행 판정"""
    HARD_BLOCK = "hard_block"               # fidelity ≥ 0.9 + INFEASIBLE
    CONDITIONAL_BLOCK = "conditional_block"  # 0.7 ≤ fidelity < 0.9 + INFEASIBLE
    WARN_ONLY = "warn_only"                 # fidelity < 0.7 + INFEASIBLE
    PROCEED = "proceed"                     # FEASIBLE
    USER_CONFIRMATION = "user_confirmation"  # TIMEOUT + high risk


# ── Trivial Conflict (Pre-check) ────────────────────────────

@dataclass
class TrivialConflict:
    """Pre-check 단계에서 솔버 없이 탐지한 자명한 infeasibility"""
    code: str               # RESOURCE_SHORTAGE, CAPACITY_DEFICIT, EMPTY_DOMAIN, ...
    message: str            # 사용자 안내 메시지 (한국어)
    confidence: float = 1.0  # Pre-check는 항상 1.0 (수리적 확정)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "confidence": self.confidence,
            "context": self.context,
        }


# ── CP-SAT Build Report ─────────────────────────────────────

@dataclass
class CPSATBuildReport:
    """
    Canonical IR → CP-SAT 변환 결과 + 손실 리포트.

    Presolve Soundness Policy:
      - FEASIBLE in presolve ≠ guaranteed feasible in solver
      - 생략/근사된 제약이 있으면 결과 신뢰도 하락
    """
    supported_constraints: List[str] = field(default_factory=list)
    relaxed_constraints: List[str] = field(default_factory=list)
    dropped_constraints: List[str] = field(default_factory=list)

    # 변환 충실도 (1.0 = 완전, 0.0 = 대부분 생략)
    fidelity_score: float = 1.0
    fidelity_note: str = ""

    # 생략 영향도 평가
    dropped_impact_level: DroppedImpactLevel = DroppedImpactLevel.LOW
    dropped_impact_note: str = ""

    # CP-SAT 제약 index → canonical 제약 이름 매핑
    constraint_name_map: Dict[int, str] = field(default_factory=dict)

    # 변수/제약 수
    variable_count: int = 0
    constraint_count: int = 0

    def to_dict(self) -> dict:
        return {
            "supported_count": len(self.supported_constraints),
            "relaxed_count": len(self.relaxed_constraints),
            "dropped_count": len(self.dropped_constraints),
            "dropped_constraints": self.dropped_constraints,
            "fidelity_score": round(self.fidelity_score, 3),
            "fidelity_note": self.fidelity_note,
            "dropped_impact_level": self.dropped_impact_level.value,
            "dropped_impact_note": self.dropped_impact_note,
            "variable_count": self.variable_count,
            "constraint_count": self.constraint_count,
        }


# ── Quick Solve Result ───────────────────────────────────────

@dataclass
class QuickSolveResult:
    """CP-SAT Quick Solve 결과"""
    status: PresolveStatus
    elapsed_sec: float = 0.0
    solver_stats: Dict[str, Any] = field(default_factory=dict)
    fidelity_score: float = 1.0
    dropped_constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "solver_stats": self.solver_stats,
            "fidelity_score": round(self.fidelity_score, 3),
        }


# ── Timeout Assessment ───────────────────────────────────────

@dataclass
class TimeoutAssessment:
    """TIMEOUT 발생 시 risk 평가"""
    risk_score: float = 0.0         # 0.0 ~ 1.0
    risk_factors: List[str] = field(default_factory=list)
    action: str = "proceed_with_warning"  # user_confirmation | proceed_with_warning
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "risk_score": round(self.risk_score, 3),
            "risk_factors": self.risk_factors,
            "action": self.action,
            "message": self.message,
        }


# ── Conflict Entry ───────────────────────────────────────────

@dataclass
class ConflictEntry:
    """개별 충돌 제약 정보"""
    constraint: str                             # 제약 이름
    type: str = "hard"                          # hard | soft
    confidence: float = 0.0                     # 0.0 ~ 1.0
    overlapping_variables: List[str] = field(default_factory=list)
    conflict_pair: Optional[str] = None         # 가장 강하게 충돌하는 상대 제약
    reason: str = ""                            # 자동 생성된 충돌 설명

    def to_dict(self) -> dict:
        d = {
            "constraint": self.constraint,
            "type": self.type,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }
        if self.overlapping_variables:
            d["overlapping_variables"] = self.overlapping_variables
        if self.conflict_pair:
            d["conflict_pair"] = self.conflict_pair
        return d


# ── Soft Test Result ─────────────────────────────────────────

@dataclass
class SoftTestResult:
    """Soft 제약 제거 테스트 결과 (참고용)"""
    soft_only_feasible: bool = False
    note: str = (
        "soft 제거 결과는 참고용이며, soft ≠ optional입니다. "
        "soft_critical 제약이 포함되어 있을 수 있습니다."
    )

    def to_dict(self) -> dict:
        return {
            "soft_only_feasible": self.soft_only_feasible,
            "note": self.note,
        }


# ── Conflict Diagnosis ───────────────────────────────────────

@dataclass
class ConflictDiagnosis:
    """충돌 진단 결과 (QuickXPlain 기반)"""
    # 최소 충돌 후보 집합 — 보장 수준은 guarantee_level로 명시
    conflict_candidate_set: List[str] = field(default_factory=list)
    guarantee_level: GuaranteeLevel = GuaranteeLevel.NONE

    # 개별 충돌 정보
    conflicts: List[ConflictEntry] = field(default_factory=list)

    # Soft 제거 테스트 (참고용)
    soft_test: Optional[SoftTestResult] = None

    # 충돌 쌍 (변수 공유 기반)
    conflict_pairs: List[Dict[str, Any]] = field(default_factory=list)

    # 메타
    solve_count: int = 0
    max_depth_reached: int = 0
    total_elapsed_sec: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "conflict_candidate_set": self.conflict_candidate_set,
            "guarantee_level": self.guarantee_level.value,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "solve_count": self.solve_count,
            "max_depth_reached": self.max_depth_reached,
            "total_elapsed_sec": round(self.total_elapsed_sec, 3),
        }
        if self.soft_test:
            d["soft_test"] = self.soft_test.to_dict()
        if self.conflict_pairs:
            d["conflict_pairs"] = self.conflict_pairs
        return d


# ── Presolve Result (최종) ───────────────────────────────────

@dataclass
class PresolveResult:
    """Stage 5 Presolve Probing 최종 결과"""
    status: PresolveStatus
    phase: str = ""                             # cache_hit | pre_check | quick_solve | conflict_detection
    elapsed_sec: float = 0.0
    request_id: str = ""                        # Observability 추적용

    # Fidelity 기반 판정
    decision: FidelityDecision = FidelityDecision.PROCEED
    decision_message: str = ""

    # Step 2: Pre-check
    trivial_issues: List[TrivialConflict] = field(default_factory=list)

    # Step 3: 변환 리포트
    build_report: Optional[CPSATBuildReport] = None

    # Step 4: Quick Solve 통계
    quick_solve: Optional[QuickSolveResult] = None

    # Step 4-b: Timeout risk
    timeout_assessment: Optional[TimeoutAssessment] = None

    # Step 5: Conflict Detection (INFEASIBLE 시만)
    conflict_diagnosis: Optional[ConflictDiagnosis] = None

    # 캐시
    cached: bool = False
    cache_key: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "status": self.status.value,
            "phase": self.phase,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "request_id": self.request_id,
            "decision": self.decision.value,
            "decision_message": self.decision_message,
            "cached": self.cached,
        }
        if self.trivial_issues:
            d["trivial_issues"] = [t.to_dict() for t in self.trivial_issues]
        if self.build_report:
            d["build_report"] = self.build_report.to_dict()
        if self.quick_solve:
            d["quick_solve"] = self.quick_solve.to_dict()
        if self.timeout_assessment:
            d["timeout_assessment"] = self.timeout_assessment.to_dict()
        if self.conflict_diagnosis:
            d["conflict_diagnosis"] = self.conflict_diagnosis.to_dict()
        if self.cache_key:
            d["cache_key"] = self.cache_key
        return d


# ── Cache Key Builder ────────────────────────────────────────

def build_cache_key(
    math_model: Dict,
    bound_data: Dict,
    policy_version: str = "",
    catalog_version: str = "",
) -> str:
    """
    캐시 키 생성 = IR 해시 + 정책 버전 + 카탈로그 버전.

    무효화 조건: canonical IR, policies.yaml, parameter_catalog.yaml 변경 시.
    """
    import json
    # IR의 핵심 요소만 해싱 (solution은 제외)
    key_parts = {
        "constraints": math_model.get("constraints", []),
        "variables": math_model.get("variables", []),
        "objective": math_model.get("objective", {}),
        "parameters": sorted(bound_data.get("parameters", {}).items()),
        "set_sizes": sorted(bound_data.get("set_sizes", {}).items()),
    }
    ir_bytes = json.dumps(key_parts, sort_keys=True, default=str).encode("utf-8")
    ir_hash = hashlib.sha256(ir_bytes).hexdigest()[:16]
    return f"{ir_hash}:{policy_version}:{catalog_version}"


# ── Presolve Configuration ───────────────────────────────────

@dataclass
class PresolveConfig:
    """Presolve 실행 설정 (환경변수로 오버라이드 가능)"""
    # Quick Solve
    quick_solve_time_limit_sec: int = 10
    quick_solve_num_workers: int = 1

    # QuickXPlain 가드
    max_solves: int = 50
    max_depth: int = 10
    conflict_time_budget_sec: float = 20.0
    per_solve_limit_sec: float = 3.0

    # Skip 조건
    skip_threshold_constraints: int = 10

    # Fidelity 임계치 (코드로 강제)
    fidelity_high: float = 0.9      # ≥ 이면 hard block 허용
    fidelity_medium: float = 0.7    # ≥ 이면 conditional block
    # < fidelity_medium → warn only

    # TIMEOUT risk
    risk_high_threshold: float = 0.5  # ≥ 이면 user confirmation

    # 캐시
    cache_enabled: bool = True

    @classmethod
    def from_env(cls) -> PresolveConfig:
        """환경변수에서 설정 로딩"""
        import os
        config = cls()
        config.quick_solve_time_limit_sec = int(
            os.environ.get("PRESOLVE_TIME_LIMIT", config.quick_solve_time_limit_sec)
        )
        config.max_solves = int(
            os.environ.get("PRESOLVE_MAX_SOLVES", config.max_solves)
        )
        config.conflict_time_budget_sec = float(
            os.environ.get("PRESOLVE_CONFLICT_BUDGET", config.conflict_time_budget_sec)
        )
        config.skip_threshold_constraints = int(
            os.environ.get("PRESOLVE_SKIP_THRESHOLD", config.skip_threshold_constraints)
        )
        config.cache_enabled = os.environ.get("PRESOLVE_CACHE", "true").lower() == "true"
        return config
