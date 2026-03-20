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

        # 정수 스케일링 (#5: 정규화 후 1~1000 범위)
        vals = list(scores.values())
        min_s = min(vals) if vals else 0
        max_s = max(vals) if vals else 1
        range_s = max(max_s - min_s, 1e-6)

        int_scores = {}
        for col_id, score in scores.items():
            norm = (score - min_s) / range_s
            int_scores[col_id] = max(1, int(1 + norm * 999))

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

        # #4: idle 정규화용 최대값
        max_idle = max((c.idle_minutes for c in self.columns), default=1) or 1

        for col in self.columns:
            # 기본: duty 1개 = 1.0
            score = cfg.duty_weight

            # secondary: 짧은 duty penalty (비선형)
            tc = len(col.trips)
            short_penalty = max(0, cfg.short_threshold - tc) ** 2

            # trip 수 비선형 보너스: 10/tc (10-trip=1.0, 5-trip=2.0, 1-trip=10.0)
            trip_inefficiency = 10.0 / max(tc, 1)

            # idle penalty (정규화: 0~1 범위)
            idle_norm = col.idle_minutes / max_idle

            score += cfg.short_penalty_weight * short_penalty
            score += 0.1 * trip_inefficiency
            score += cfg.idle_penalty_weight * idle_norm

            scores[col.id] = score

        return scores

    # ── balance_workload: 워크로드 균등화 ───────────────────

    def _balance_workload(self, params: Dict) -> Dict[int, float]:
        """
        minimize ∑ z[k] + λ * ∑ balance_penalty[k] * z[k]

        trip 수가 target에 가까울수록 낮은 cost.
        """
        cfg = self.config

        # target trips per duty (#3: 하드코딩 제거)
        # 전체 unique task 수를 column pool에서 계산
        all_tasks = set()
        for c in self.columns:
            all_tasks.update(c.trips)
        total_task_count = len(all_tasks)

        target_duties = params.get("total_duties")
        if target_duties:
            target_trips = math.ceil(total_task_count / int(target_duties))
        else:
            target_trips = round(total_task_count / max(len(self.columns), 1)) or 8

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

        # #6: 정규화용 최대값
        max_idle = max((c.idle_minutes for c in self.columns), default=1) or 1
        max_span = max((c.span_minutes for c in self.columns), default=1) or 1

        for col in self.columns:
            idle_norm = col.idle_minutes / max_idle
            dead_span = max(0, col.span_minutes - col.active_minutes)
            dead_norm = dead_span / max_span

            score = 0.1 * cfg.duty_weight + idle_norm + 0.5 * dead_norm
            scores[col.id] = max(score, 0.01)

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
