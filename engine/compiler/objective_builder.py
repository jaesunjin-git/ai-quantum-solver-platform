"""
objective_builder.py ──────────────────────────────────────
Solver-independent Objective Layer.

사용자 목적함수 → column별 score 계산.
모든 solver backend(CP-SAT, D-Wave, Gurobi)가 동일한 score를 사용.

구조:
  [User Objective] → ObjectiveBuilder → {col_id: score} → Solver

score는 단일 스칼라: minimize ∑ score[k] * z[k]

목적함수별 score 정의:
  minimize_duties:     1 + ε * quality_cost
  balance_workload:    1 + λ * balance_penalty + ε * quality_cost
  maximize_efficiency: idle_minutes (duty 수 무관)
  minimize_cost:       custom cost
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.column_generator import FeasibleColumn

logger = logging.getLogger(__name__)


# ── Objective 설정 (config-driven) ────────────────────────────

@dataclass
class ObjectiveConfig:
    """목적함수 가중치 설정"""
    # 기본 가중치
    duty_weight: float = 1.0          # duty 수 최소화 가중치
    short_penalty_weight: float = 0.05  # 짧은 duty 억제
    idle_penalty_weight: float = 0.01   # idle 시간 억제
    balance_penalty_weight: float = 0.5  # 워크로드 균형

    # 짧은 duty 기준
    short_threshold: int = 8           # 이 trip 수 미만이면 penalty

    @classmethod
    def from_params(cls, params: Dict[str, Any]) -> "ObjectiveConfig":
        """파라미터에서 로딩 (향후 YAML config 지원)"""
        cfg = cls()
        for attr in ['duty_weight', 'short_penalty_weight', 'idle_penalty_weight',
                      'balance_penalty_weight', 'short_threshold']:
            val = params.get(f'objective_{attr}')
            if val is not None:
                setattr(cfg, attr, val)
        return cfg


# ── Objective Builder ─────────────────────────────────────────

class ObjectiveBuilder:
    """
    Solver-independent 목적함수 구축.

    Usage:
        builder = ObjectiveBuilder(columns)
        scores = builder.build("minimize_duties")
        # scores: {col_id: score_int}
        # solver: minimize ∑ scores[k] * z[k]
    """

    def __init__(self, columns: List[FeasibleColumn], config: Optional[ObjectiveConfig] = None):
        self.columns = columns
        self.config = config or ObjectiveConfig()

    def build(self, objective_type: str, params: Optional[Dict] = None) -> Dict[int, int]:
        """
        목적함수별 column score 계산.

        Args:
            objective_type: 목적함수 ID
            params: 추가 파라미터 (target_duties 등)

        Returns:
            {column_id: score_int} — 정수 스케일링 (solver 호환)
        """
        params = params or {}

        if objective_type in ("minimize_duties", "minimize_duties_with_penalties"):
            scores = self._minimize_duties(params)
        elif objective_type == "balance_workload":
            scores = self._balance_workload(params)
        elif objective_type == "maximize_efficiency":
            scores = self._maximize_efficiency(params)
        elif objective_type == "minimize_cost":
            scores = self._minimize_cost(params)
        else:
            # fallback: minimize_duties
            logger.warning(f"Unknown objective '{objective_type}', using minimize_duties")
            scores = self._minimize_duties(params)

        # 정수 스케일링 (CP-SAT, QUBO 모두 정수 필요)
        int_scores = {}
        for col_id, score in scores.items():
            int_scores[col_id] = max(1, int(score * 1000))

        logger.info(f"Objective '{objective_type}': "
                     f"score range [{min(int_scores.values())}..{max(int_scores.values())}], "
                     f"{len(int_scores)} columns")

        return int_scores

    # ── minimize_duties: duty 수 최소화 + quality cost ──────

    def _minimize_duties(self, params: Dict) -> Dict[int, float]:
        """
        minimize ∑ z[k] + ε * ∑ quality_cost[k] * z[k]

        duty 수가 동일하면 quality가 좋은 것 선택.
        """
        cfg = self.config
        scores = {}

        for col in self.columns:
            # 기본: duty 1개 = 1.0
            score = cfg.duty_weight

            # secondary: 짧은 duty penalty (비선형)
            tc = len(col.trips)
            short_penalty = max(0, cfg.short_threshold - tc) ** 2

            # trip 수 비선형 보너스: 10/tc (10-trip=1.0, 5-trip=2.0, 1-trip=10.0)
            # set partitioning에서 선형 보너스는 효과 없으므로 역수형 사용
            trip_inefficiency = 10.0 / max(tc, 1)

            # idle penalty
            idle_penalty = col.idle_minutes

            score += cfg.short_penalty_weight * short_penalty
            score += 0.1 * trip_inefficiency  # 짧은 duty일수록 비용 증가
            score += cfg.idle_penalty_weight * idle_penalty

            scores[col.id] = score

        return scores

    # ── balance_workload: 워크로드 균등화 ───────────────────

    def _balance_workload(self, params: Dict) -> Dict[int, float]:
        """
        minimize ∑ z[k] + λ * ∑ balance_penalty[k] * z[k]

        trip 수가 target에 가까울수록 낮은 cost.
        """
        cfg = self.config

        # target trips per duty
        total_trips = sum(len(c.trips) for c in self.columns) // max(len(self.columns), 1)
        # 사용자 지정 target 또는 자동 계산
        target_duties = params.get("total_duties")
        if target_duties:
            target_trips = math.ceil(320 / int(target_duties))  # 근사
        else:
            target_trips = 8  # 기본

        scores = {}
        for col in self.columns:
            tc = len(col.trips)

            # duty 수 최소화
            score = cfg.duty_weight

            # 균형 penalty: target에서 벗어날수록 높은 cost
            balance_penalty = (tc - target_trips) ** 2
            score += cfg.balance_penalty_weight * balance_penalty

            # idle penalty
            score += cfg.idle_penalty_weight * col.idle_minutes

            scores[col.id] = score

        return scores

    # ── maximize_efficiency: idle 최소화 ────────────────────

    def _maximize_efficiency(self, params: Dict) -> Dict[int, float]:
        """
        minimize ∑ idle[k] * z[k]

        duty 수보다 운행 효율 우선.
        대기시간이 적은 duty 선호.
        """
        cfg = self.config
        scores = {}

        for col in self.columns:
            # idle 시간이 주 비용 (duty 수 가중치 낮음)
            idle = col.idle_minutes
            dead_span = max(0, col.span_minutes - col.active_minutes)

            score = 0.1 * cfg.duty_weight + idle + 0.5 * dead_span
            scores[col.id] = max(score, 0.1)

        return scores

    # ── minimize_cost: custom cost ──────────────────────────

    def _minimize_cost(self, params: Dict) -> Dict[int, float]:
        """
        minimize ∑ cost[k] * z[k]

        column의 원래 cost 사용 (야간 수당, 피로도 등 반영 가능).
        """
        scores = {}
        for col in self.columns:
            scores[col.id] = max(col.cost, 0.1)
        return scores


# ── Objective ID 추출 헬퍼 ────────────────────────────────────

def extract_objective_type(math_model: Dict) -> str:
    """math_model에서 objective type ID 추출"""
    obj = math_model.get("objective", {})

    # objective에 id가 있으면 사용
    obj_id = obj.get("id", obj.get("name", ""))
    if obj_id:
        return obj_id

    # description 기반 fallback
    desc = obj.get("description", "").lower()
    if "효율" in desc or "efficiency" in desc:
        return "maximize_efficiency"
    if "균형" in desc or "balance" in desc or "균등" in desc:
        return "balance_workload"
    if "비용" in desc or "cost" in desc:
        return "minimize_cost"

    return "minimize_duties"  # default
