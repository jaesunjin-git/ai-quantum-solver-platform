"""
Temporal Type System — axis type definitions and lint.

Three temporal types:
  raw_clock_minute   : Original clock time (0~1439)
  service_day_minute : Absolute time on service-day axis (anchor-based unwrap)
  duration_minute    : Interval length (axis-independent, no transform needed)
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class TemporalType(Enum):
    RAW_CLOCK_MINUTE = "raw_clock_minute"
    SERVICE_DAY_MINUTE = "service_day_minute"
    DURATION_MINUTE = "duration_minute"


# Compatible comparison/operation pairs
COMPATIBLE_PAIRS = frozenset({
    (TemporalType.SERVICE_DAY_MINUTE, TemporalType.SERVICE_DAY_MINUTE),
    (TemporalType.DURATION_MINUTE, TemporalType.DURATION_MINUTE),
    (TemporalType.RAW_CLOCK_MINUTE, TemporalType.RAW_CLOCK_MINUTE),
    # abs + duration = abs (allowed: duty_start + preparation_minutes)
    (TemporalType.SERVICE_DAY_MINUTE, TemporalType.DURATION_MINUTE),
    (TemporalType.DURATION_MINUTE, TemporalType.SERVICE_DAY_MINUTE),
})


def parse_temporal_type(type_str: str) -> TemporalType:
    """Parse a temporal type string, with fallback to RAW_CLOCK_MINUTE."""
    try:
        return TemporalType(type_str)
    except ValueError:
        return TemporalType.RAW_CLOCK_MINUTE


def lint_temporal_comparison(
    field_a: str, field_b: str, types: dict[str, str]
) -> Optional[str]:
    """Detect axis mismatch between two fields.

    Returns error message if incompatible, None if OK.
    """
    str_a = types.get(field_a)
    str_b = types.get(field_b)

    # If either field has no registered type, skip lint
    if str_a is None or str_b is None:
        return None

    type_a = parse_temporal_type(str_a)
    type_b = parse_temporal_type(str_b)

    if (type_a, type_b) not in COMPATIBLE_PAIRS:
        return (
            f"Temporal type mismatch: {field_a}({type_a.value}) "
            f"vs {field_b}({type_b.value})"
        )
    return None
