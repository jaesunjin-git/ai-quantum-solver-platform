"""
hybrid_strategy.py ────────────────────────────────────────
CQM → CP-SAT Hybrid 전략 orchestrator.

CQM의 warm start 해를 CP-SAT에 hint로 주입하여
탐색 공간을 축소하고 최적화 시간을 단축.

GR-1: engine 내부 모듈. domain import 없음.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────

@dataclass
class HybridConfig:
    """Hybrid 전략 설정 (YAML 외부화)."""
    mode: str = "cqm_then_cpsat"
    cqm_time_fraction: float = 0.3
    cqm_min_time_sec: int = 60
    cpsat_min_time_sec: int = 120
    total_default_time_sec: int = 720
    use_same_column_pool: bool = True
    use_objective_bound: bool = False
    min_hint_quality: float = 0.0
    fallback_on_cqm_failure: bool = True

    @classmethod
    def load(cls) -> "HybridConfig":
        """configs/hybrid_strategy.yaml에서 로딩."""
        cfg = cls()
        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "configs", "hybrid_strategy.yaml"
        )
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            section = data.get("quantum_warmstart", {})
            for attr in [
                "cqm_time_fraction", "cqm_min_time_sec", "cpsat_min_time_sec",
                "total_default_time_sec", "use_same_column_pool",
                "use_objective_bound", "min_hint_quality",
                "fallback_on_cqm_failure",
            ]:
                val = section.get(attr)
                if val is not None:
                    setattr(cfg, attr, val)
            cfg.mode = section.get("default_mode", cfg.mode)
        except Exception as e:
            logger.warning(f"HybridConfig load failed, using defaults: {e}")
        return cfg


# ── Result ────────────────────────────────────────────────────

@dataclass
class HybridPhaseResult:
    """한 phase의 실행 결과 요약."""
    solver: str = ""
    status: str = ""
    objective_value: Optional[float] = None
    time_sec: float = 0.0
    selected_columns: int = 0


@dataclass
class HybridResult:
    """Hybrid 전략 전체 결과."""
    cqm_phase: Optional[HybridPhaseResult] = None
    cpsat_phase: Optional[HybridPhaseResult] = None
    hints_injected: int = 0
    hints_skipped_reason: str = ""
    strategy_used: str = ""  # "hybrid_warmstart" | "cpsat_fallback"
    improvement_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """프론트엔드용 직렬화."""
        result = {"strategy_used": self.strategy_used}
        if self.cqm_phase:
            result["cqm_phase"] = {
                "solver": self.cqm_phase.solver,
                "status": self.cqm_phase.status,
                "objective_value": self.cqm_phase.objective_value,
                "time_sec": round(self.cqm_phase.time_sec, 1),
                "selected_columns": self.cqm_phase.selected_columns,
            }
        if self.cpsat_phase:
            result["cpsat_phase"] = {
                "solver": self.cpsat_phase.solver,
                "status": self.cpsat_phase.status,
                "objective_value": self.cpsat_phase.objective_value,
                "time_sec": round(self.cpsat_phase.time_sec, 1),
                "selected_columns": self.cpsat_phase.selected_columns,
            }
        result["hints_injected"] = self.hints_injected
        if self.hints_skipped_reason:
            result["hints_skipped_reason"] = self.hints_skipped_reason
        if self.improvement_pct is not None:
            result["improvement_pct"] = round(self.improvement_pct, 1)
        return result


# ── Hint 주입 ─────────────────────────────────────────────────

def inject_warmstart_hints(
    cpsat_model,
    cpsat_z_map: Dict[int, Any],
    cqm_solution: Dict[str, Any],
    config: HybridConfig,
    total_duties: Optional[int] = None,
) -> tuple:
    """CQM raw solution을 CP-SAT hint로 주입.

    Args:
        cpsat_model: CP-SAT CpModel 객체
        cpsat_z_map: {col_id(int): BoolVar} — CP-SAT 변수 매핑
        cqm_solution: CQM executor 반환값의 solution dict
        config: HybridConfig
        total_duties: 목표 duty 수 (hint 품질 판정용)

    Returns:
        (hints_injected: int, skip_reason: str)
        skip_reason이 비어있으면 정상 주입, 아니면 skip 사유
    """
    z_values = cqm_solution.get("z", {})
    if not z_values:
        return 0, "CQM solution has no z values"

    # 품질 필터: 선택된 column 수 / 목표 duty 수
    selected_count = sum(1 for v in z_values.values() if int(v) > 0)
    if config.min_hint_quality > 0 and total_duties and total_duties > 0:
        quality = selected_count / total_duties
        if quality < config.min_hint_quality:
            return 0, (
                f"Hint quality too low: {selected_count}/{total_duties} "
                f"= {quality:.2f} < {config.min_hint_quality}"
            )

    # Hint 주입: CQM의 z값을 CP-SAT 변수에 매핑
    count = 0
    for col_id, var in cpsat_z_map.items():
        # CQM executor는 string key로 반환 → str(col_id)로 조회
        cqm_val = z_values.get(str(col_id), 0)
        cpsat_model.AddHint(var, int(cqm_val))
        count += 1

    logger.info(
        f"Hybrid hint: {count} hints injected "
        f"(CQM selected {selected_count} columns)"
    )
    return count, ""


# ── 시간 예산 계산 ────────────────────────────────────────────

def compute_time_budget(
    total_time_sec: int, config: HybridConfig, elapsed_sec: float = 0.0
) -> Dict[str, int]:
    """CQM/CP-SAT 시간 배분 계산.

    Returns:
        {"cqm": int, "cpsat": int, "viable": bool}
    """
    remaining = total_time_sec - elapsed_sec
    cqm_time = max(
        config.cqm_min_time_sec,
        int(remaining * config.cqm_time_fraction)
    )
    cpsat_time = int(remaining - cqm_time)

    viable = cpsat_time >= config.cpsat_min_time_sec
    if not viable:
        logger.warning(
            f"Hybrid: insufficient time for CP-SAT "
            f"(remaining={remaining:.0f}s, cqm={cqm_time}s, "
            f"cpsat={cpsat_time}s < min {config.cpsat_min_time_sec}s)"
        )

    return {"cqm": cqm_time, "cpsat": cpsat_time, "viable": viable}
