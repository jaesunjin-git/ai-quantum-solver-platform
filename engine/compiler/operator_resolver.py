"""
operator_resolver.py ────────────────────────────────────────
제약의 실효 operator를 결정하는 범용 resolver.

모든 컴파일러(CP-SAT SP, DWave CQM, 향후 Gurobi 등)가 공통 사용.

우선순위:
  1. run_config에서 사용자가 지정한 override (향후 UI)
  2. constraints.yaml의 objective_operator_override
  3. constraints.yaml의 structured.operator 또는 expression_template에서 추출
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConstraintOperatorResolver:
    """제약의 실효 operator를 결정하는 범용 resolver."""

    def __init__(self, objective_name: str, run_config: Optional[Dict] = None):
        self.objective_name = objective_name
        self.run_config = run_config or {}

    def resolve(self, constraint_id: str, constraint_def: Dict) -> str:
        """제약 ID와 정의에서 실효 operator 결정.

        Args:
            constraint_id: 제약 ID (예: "fixed_total_duties")
            constraint_def: constraints.yaml의 제약 정의 dict

        Returns:
            operator 문자열: "==", "<=", ">="
        """
        # 1) run_config override (사용자 UI에서 설정, 향후)
        user_overrides = self.run_config.get("operator_overrides", {})
        if constraint_id in user_overrides:
            op = user_overrides[constraint_id]
            logger.debug(f"Operator '{constraint_id}': '{op}' (user override)")
            return op

        # 2) YAML objective_operator_override
        obj_overrides = constraint_def.get("objective_operator_override", {})
        if self.objective_name in obj_overrides:
            op = obj_overrides[self.objective_name]
            logger.debug(
                f"Operator '{constraint_id}': '{op}' "
                f"(objective_override for '{self.objective_name}')"
            )
            return op

        # 3) default: structured.operator 또는 expression_template에서 추출
        structured = constraint_def.get("structured", {})
        if structured.get("operator"):
            return structured["operator"]

        expr = constraint_def.get("expression_template", "")
        for op in ["<=", ">=", "=="]:
            if op in expr:
                return op

        return "=="
