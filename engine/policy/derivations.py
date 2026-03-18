"""
Derivation function registry for canonical field generation.

Each derivation takes raw value(s) + policy config and returns canonical value.
"""
from __future__ import annotations

from typing import Any, Callable


def cyclic_unwrap(value: float, anchor: float, period: float) -> float:
    """shift_if_before_anchor: value < anchor → value + period.

    Example: 316 (05:16) with anchor=1020, period=1440 → 1756
    """
    if value < anchor:
        return value + period
    return value


def day_offset(value: float, anchor: float, **_kwargs) -> int:
    """0 = same day (value >= anchor), 1 = next day (value < anchor)."""
    return 0 if value >= anchor else 1


def interval_crosses_period(dep: float, arr: float, **_kwargs) -> bool:
    """True if arrival is before departure (midnight crossing)."""
    return arr < dep


def window_membership(value: float, start: float, end: float, **_kwargs) -> bool:
    """True if value is within [start, end]."""
    return start <= value <= end


# Registry: derivation_name → function
DERIVATION_REGISTRY: dict[str, Callable] = {
    "cyclic_unwrap": cyclic_unwrap,
    "day_offset": day_offset,
    "interval_crosses_period": interval_crosses_period,
    "window_membership": window_membership,
}
