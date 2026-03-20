"""
Stage 3 — 파라미터 교차 규칙 검증기 (플랫폼 공통).

설계 원칙:
  - 플랫폼이 규칙을 실행하고, 도메인이 규칙을 정의 (cross_rules.yaml)
  - knowledge/domains/{domain}/cross_rules.yaml에서 규칙을 로드
  - 수식 평가기: 사칙연산(+, -, *, /)과 비교 연산 지원
  - 파라미터 참조: ${param_name}을 context["parameters"]에서 해석
  - 이 파일에 도메인 고유 로직 없음

기대하는 context 키:
    parameters: dict          — {param_id: {value, source, ...}} 또는 {param_id: value}
    domain: str               — domain identifier (e.g., "railway")
    confirmed_problem: dict   — optional, user-confirmed problem definition
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from engine.validation.base import AutoFix, BaseValidator, ValidationResult

logger = logging.getLogger(__name__)

# ── Rule file discovery ──
# GR-1: engine은 knowledge 파일시스템 레이아웃을 몰라야 함.
# context["knowledge_base_path"] 또는 context["cross_rules_data"]로 주입 권장.
# fallback: 기존 경로 (하위 호환)

_FALLBACK_KNOWLEDGE_BASE = Path(__file__).resolve().parents[3] / "knowledge" / "domains"


def _resolve_knowledge_base(context: Optional[dict] = None) -> Path:
    """context에서 knowledge_base_path를 가져오거나, fallback 사용."""
    if context:
        kb = context.get("knowledge_base_path")
        if kb:
            return Path(kb)
    return _FALLBACK_KNOWLEDGE_BASE


def _find_cross_rules(domain: str, knowledge_base: Optional[Path] = None) -> Optional[Path]:
    """Find cross_rules.yaml for a given domain."""
    base = knowledge_base or _FALLBACK_KNOWLEDGE_BASE
    candidates = [
        base / domain / "cross_rules.yaml",
        base / domain / "cross_rules.yml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_rules(domain: str, context: Optional[dict] = None) -> list[dict]:
    """Load and parse cross-validation rules for a domain."""
    # context에 이미 로드된 rules가 있으면 사용 (GR-1 권장 방식)
    if context:
        preloaded = context.get("cross_rules_data")
        if preloaded is not None:
            return preloaded if isinstance(preloaded, list) else preloaded.get("rules", [])

    kb = _resolve_knowledge_base(context)
    path = _find_cross_rules(domain, kb)
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("rules", []) if data else []
    except Exception as e:
        logger.warning(f"Failed to load cross_rules.yaml for {domain}: {e}")
        return []


# ── Expression evaluator ──

_PARAM_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_params(expression: str, params: dict[str, float]) -> Optional[str]:
    """Replace ${param_name} with numeric values. Returns None if any param missing."""
    missing = []

    def replacer(match):
        name = match.group(1)
        val = params.get(name)
        if val is None:
            missing.append(name)
            return "0"
        return str(float(val))

    resolved = _PARAM_RE.sub(replacer, expression)
    return None if missing else resolved


def _safe_eval(expression: str) -> Optional[bool]:
    """Evaluate a simple arithmetic/comparison expression safely.

    Supports: numbers, +, -, *, /, <, <=, >, >=, ==, !=, (, )
    No function calls, no attribute access, no imports.
    """
    # Whitelist: only digits, operators, dots, spaces, parens
    if re.search(r"[a-zA-Z_]", expression):
        return None
    try:
        # Restrict to safe builtins
        result = eval(expression, {"__builtins__": {}}, {})
        return bool(result)
    except Exception:
        return None


def _extract_param_values(parameters: dict) -> dict[str, float]:
    """Normalize parameter dict to {name: float_value}.

    Handles both flat dicts {name: value} and nested {name: {value: ..., source: ...}}.
    """
    result = {}
    for key, val in parameters.items():
        if isinstance(val, dict):
            v = val.get("value")
        else:
            v = val
        if v is not None:
            try:
                result[key] = float(v)
            except (ValueError, TypeError):
                pass
    return result


# ── Validator ──

class ParameterCrossRuleValidator(BaseValidator):
    """Evaluates domain-defined cross-validation rules on parameters.

    Rules are loaded from knowledge/domains/{domain}/cross_rules.yaml.
    Each rule's condition is evaluated with the current parameter values.
    Failed rules produce errors or warnings based on their severity.
    """

    stage = 3
    name = "ParameterCrossRuleValidator"
    description = "파라미터 교차 검증 (도메인 규칙 기반)"

    def validate(self, context: dict) -> ValidationResult:
        result = self._make_result()

        domain = context.get("domain", "")
        if not domain:
            return result

        rules = _load_rules(domain, context)
        if not rules:
            return result

        # Extract parameter values
        raw_params = context.get("parameters", {})
        if not raw_params:
            # Try from confirmed_problem
            confirmed = context.get("confirmed_problem", {})
            raw_params = confirmed.get("parameters", {}) if confirmed else {}

        params = _extract_param_values(raw_params)
        if not params:
            return result

        for rule in rules:
            rule_id = rule.get("id", "unknown")
            condition = rule.get("condition", "")
            severity = rule.get("severity", "warning")
            description = rule.get("description", "")
            suggestion = rule.get("suggestion")

            # Resolve parameter references
            resolved = _resolve_params(condition, params)
            if resolved is None:
                # Missing parameters — skip silently (may not apply to this sub-domain)
                continue

            # Evaluate condition
            passed = _safe_eval(resolved)
            if passed is None:
                logger.warning(f"Cross-rule '{rule_id}' eval failed: {resolved}")
                continue

            if not passed:
                # Build auto-fix if defined
                auto_fix = None
                fix_def = rule.get("auto_fix")
                if fix_def:
                    fix_expr = fix_def.get("expression", "")
                    resolved_fix = _resolve_params(fix_expr, params)
                    new_val = None
                    if resolved_fix:
                        try:
                            new_val = eval(resolved_fix, {"__builtins__": {}}, {})
                        except Exception:
                            pass

                    auto_fix = AutoFix(
                        param=fix_def.get("param", ""),
                        old_val=params.get(fix_def.get("param", "")),
                        new_val=new_val,
                        action=fix_def.get("action", "set"),
                        label=fix_def.get("label"),
                    )

                code = f"CROSS_RULE_{rule_id.upper()}"
                ctx = {"rule_id": rule_id, "condition": condition}

                if severity == "error":
                    result.add_error(
                        code=code,
                        message=description,
                        suggestion=suggestion,
                        auto_fix=auto_fix,
                        context=ctx,
                    )
                else:
                    result.add_warning(
                        code=code,
                        message=description,
                        suggestion=suggestion,
                        auto_fix=auto_fix,
                        context=ctx,
                    )

        return result


class ParameterRangeValidator(BaseValidator):
    """Validates parameter values against reference ranges.

    Uses reference_ranges.yaml from the domain knowledge base.
    """

    stage = 3
    name = "ParameterRangeValidator"
    description = "파라미터 참조 범위 검증"

    def validate(self, context: dict) -> ValidationResult:
        result = self._make_result()

        domain = context.get("domain", "")
        if not domain:
            return result

        # Load reference ranges (context 주입 또는 fallback)
        kb = _resolve_knowledge_base(context)
        ref_path = kb / domain / "reference_ranges.yaml"
        if not ref_path.exists():
            return result

        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                ref_data = yaml.safe_load(f) or {}
        except Exception:
            return result

        # Collect all reference values across sub-domains for min/max bounds
        all_mins: dict[str, float] = {}
        all_maxs: dict[str, float] = {}
        for sub_domain_data in ref_data.values():
            values = sub_domain_data.get("values", {})
            for param, val in values.items():
                try:
                    v = float(val)
                except (ValueError, TypeError):
                    continue
                if param not in all_mins or v < all_mins[param]:
                    all_mins[param] = v
                if param not in all_maxs or v > all_maxs[param]:
                    all_maxs[param] = v

        # Extract current parameters
        raw_params = context.get("parameters", {})
        params = _extract_param_values(raw_params)

        for param, val in params.items():
            if param not in all_mins:
                continue

            lo = all_mins[param]
            hi = all_maxs[param]

            # Allow 20% margin beyond reference range
            margin = (hi - lo) * 0.2 if hi != lo else hi * 0.2
            soft_lo = lo - margin
            soft_hi = hi + margin

            if val < soft_lo:
                result.add_warning(
                    code="PARAM_BELOW_RANGE",
                    message=f"'{param}' 값({val})이 참조 범위({lo}~{hi})보다 낮습니다.",
                    suggestion=f"도메인 기준 최소 {lo} 이상을 권장합니다.",
                    auto_fix=AutoFix(
                        param=param,
                        old_val=val,
                        new_val=lo,
                        action="set",
                        label=f"{param}을 {lo}으로 설정",
                    ),
                    context={"param": param, "value": val, "range_min": lo, "range_max": hi},
                )
            elif val > soft_hi:
                result.add_warning(
                    code="PARAM_ABOVE_RANGE",
                    message=f"'{param}' 값({val})이 참조 범위({lo}~{hi})보다 높습니다.",
                    suggestion=f"도메인 기준 최대 {hi} 이하를 권장합니다.",
                    auto_fix=AutoFix(
                        param=param,
                        old_val=val,
                        new_val=hi,
                        action="cap_to",
                        label=f"{param}을 {hi}으로 조정",
                    ),
                    context={"param": param, "value": val, "range_min": lo, "range_max": hi},
                )

        return result
