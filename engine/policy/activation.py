"""
Declarative activation condition evaluator.

No eval() / exec() — uses typed rule matching only.
Supports: equals, not_equals, greater_than, less_than, is_set, all_of, any_of.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_param_value(params: dict, param_id: str) -> Any:
    """Resolve a parameter value from the params dict.

    Handles both flat values and {value: ..., source: ...} dicts.
    """
    val = params.get(param_id)
    if isinstance(val, dict):
        val = val.get("value")
    return val


def evaluate_activation(rule: dict, params: dict) -> bool:
    """Evaluate a declarative activation rule against params.

    Examples:
        {param: "is_overnight_crew", equals: true}
        {param: "horizon_days", greater_than: 1}
        {all_of: [{param: "a", equals: 1}, {param: "b", equals: 2}]}
        {any_of: [{param: "x", is_set: true}]}
    """
    if not rule:
        return False

    if "param" in rule:
        val = _resolve_param_value(params, rule["param"])

        if "equals" in rule:
            return val == rule["equals"]
        if "not_equals" in rule:
            return val != rule["not_equals"]
        if "greater_than" in rule:
            try:
                return val is not None and float(val) > float(rule["greater_than"])
            except (ValueError, TypeError):
                return False
        if "less_than" in rule:
            try:
                return val is not None and float(val) < float(rule["less_than"])
            except (ValueError, TypeError):
                return False
        if "is_set" in rule:
            expected = rule["is_set"]
            if expected:
                return val is not None
            else:
                return val is None

        # param exists but no operator → treat as "is_set: true"
        return val is not None

    if "all_of" in rule:
        return all(evaluate_activation(sub, params) for sub in rule["all_of"])

    if "any_of" in rule:
        return any(evaluate_activation(sub, params) for sub in rule["any_of"])

    logger.warning(f"Unknown activation rule structure: {rule}")
    return False
