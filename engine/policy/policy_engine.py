"""
Policy Engine — Canonical Time Model + Derived Field Layer.

Reads policies.yaml from domain knowledge packs.
Resolves active policies from parameters.
Generates canonical derived fields for DataBinder.
Provides inverse transforms for result display.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from engine.policy.activation import evaluate_activation
from engine.policy.derivations import DERIVATION_REGISTRY, cyclic_unwrap
from engine.policy.temporal_types import TemporalType, lint_temporal_comparison

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(__file__).resolve().parents[2] / "knowledge" / "domains"


# ── Policy Dataclasses ──

@dataclass
class TimeAxisPolicy:
    period_minutes: int = 1440
    service_day_anchor_minute: int = 0
    horizon_days: int = 1
    timezone: str = "UTC"
    shift_policy: str = "shift_if_before_anchor"


@dataclass
class OvernightPolicy:
    active: bool = False
    min_sleep_minutes: int = 240
    sleep_window_start: int = 0
    sleep_window_end: int = 360
    sleep_counts_as_work: bool = False


@dataclass
class PolicyResolutionContext:
    """Extensible resolve interface.
    Phase 1: only clarification_params used.
    Phase 2+: regulation/customer/scenario expansion."""
    domain: str = ""
    clarification_params: dict = field(default_factory=dict)
    platform_defaults: dict = field(default_factory=dict)
    regulation_profile: Optional[dict] = None
    customer_profile: Optional[dict] = None
    scenario_overrides: Optional[dict] = None


@dataclass
class ResolvedPolicies:
    time_axis: TimeAxisPolicy = field(default_factory=TimeAxisPolicy)
    overnight: OvernightPolicy = field(default_factory=OvernightPolicy)
    policy_version: str = ""
    domain_version: str = ""
    resolution_sources: dict = field(default_factory=dict)
    resolved_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "time_axis": {
                "period_minutes": self.time_axis.period_minutes,
                "service_day_anchor_minute": self.time_axis.service_day_anchor_minute,
                "horizon_days": self.time_axis.horizon_days,
                "timezone": self.time_axis.timezone,
                "shift_policy": self.time_axis.shift_policy,
            },
            "overnight": {
                "active": self.overnight.active,
                "min_sleep_minutes": self.overnight.min_sleep_minutes,
                "sleep_window_start": self.overnight.sleep_window_start,
                "sleep_window_end": self.overnight.sleep_window_end,
                "sleep_counts_as_work": self.overnight.sleep_counts_as_work,
            },
            "policy_version": self.policy_version,
            "domain_version": self.domain_version,
            "resolution_sources": self.resolution_sources,
            "resolved_hash": self.resolved_hash,
        }


@dataclass
class DerivedFieldEntry:
    field_id: str
    count: int
    shifted_count: int
    policy_ref: str
    temporal_type: str


@dataclass
class CanonicalFieldResult:
    fields_created: list[DerivedFieldEntry] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    param_adjustments: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "fields_created": [
                {"field_id": f.field_id, "count": f.count, "shifted_count": f.shifted_count,
                 "policy_ref": f.policy_ref, "temporal_type": f.temporal_type}
                for f in self.fields_created
            ],
            "provenance_count": len(self.provenance),
            "param_adjustments": self.param_adjustments,
        }


# ── Policy Engine ──

class PolicyEngine:
    """Load domain policies, resolve active policies, generate canonical fields."""

    def __init__(self, domain: str):
        self._domain = domain
        self._raw = self._load_policies(domain)
        self._temporal_types: dict[str, str] = self._raw.get("temporal_types", {})
        self._derived_field_specs: dict[str, dict] = self._raw.get("derived_fields", {})
        self._param_canon: dict[str, dict] = self._raw.get("param_canonicalization", {})
        self._var_bound_specs: dict[str, dict] = self._raw.get("variable_bounds", {})
        self._policy_defs: dict[str, dict] = self._raw.get("policies", {})

    @staticmethod
    def _load_policies(domain: str) -> dict:
        """Load policies.yaml from domain knowledge pack."""
        if not domain:
            return {}
        path = KNOWLEDGE_BASE / domain / "policies.yaml"
        if not path.exists():
            logger.debug(f"No policies.yaml for domain '{domain}'")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.info(f"Loaded policies.yaml for domain '{domain}' (v{data.get('version', '?')})")
            return data
        except Exception as e:
            logger.warning(f"Failed to load policies.yaml for '{domain}': {e}")
            return {}

    def has_policies(self) -> bool:
        return bool(self._raw)

    # ── Resolve ──

    def resolve(self, context: PolicyResolutionContext) -> ResolvedPolicies:
        """Evaluate declarative activation conditions → active policies."""
        params = context.clarification_params
        sources: dict[str, str] = {}

        # Time axis (always active if defined)
        ta_def = self._policy_defs.get("time_axis", {})
        time_axis = TimeAxisPolicy(
            period_minutes=ta_def.get("period_minutes", 1440),
            service_day_anchor_minute=ta_def.get("service_day_anchor_minute", 0),
            horizon_days=ta_def.get("horizon_days", 1),
            timezone=ta_def.get("timezone", "UTC"),
            shift_policy=ta_def.get("shift_policy", "shift_if_before_anchor"),
        )
        sources["time_axis"] = "domain_default"

        # Overnight (conditional)
        on_def = self._policy_defs.get("overnight", {})
        on_activation = on_def.get("activation", {})
        on_active = evaluate_activation(on_activation, params) if on_activation else False

        # sleep_counts_as_work can be overridden by clarification
        scaw = on_def.get("sleep_counts_as_work", False)
        scaw_param = params.get("sleep_counts_as_work")
        if isinstance(scaw_param, dict):
            scaw = scaw_param.get("value", scaw)
        elif scaw_param is not None:
            scaw = bool(scaw_param)

        sw = on_def.get("sleep_window", {})
        overnight = OvernightPolicy(
            active=on_active,
            min_sleep_minutes=on_def.get("min_sleep_minutes", 240),
            sleep_window_start=sw.get("start", 0),
            sleep_window_end=sw.get("end", 360),
            sleep_counts_as_work=scaw,
        )
        sources["overnight.active"] = (
            "user_clarification" if on_active else "domain_default"
        )

        # Version + hash
        policy_version = str(self._raw.get("version", ""))
        domain_version = str(self._raw.get("domain", ""))

        resolved = ResolvedPolicies(
            time_axis=time_axis,
            overnight=overnight,
            policy_version=policy_version,
            domain_version=domain_version,
            resolution_sources=sources,
        )
        # Fingerprint
        hash_input = json.dumps(resolved.to_dict(), sort_keys=True, default=str)
        resolved.resolved_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        logger.info(
            f"Policy resolved: time_axis(anchor={time_axis.service_day_anchor_minute}, "
            f"horizon={time_axis.horizon_days}), "
            f"overnight.active={overnight.active}, hash={resolved.resolved_hash}"
        )
        return resolved

    # ── Canonical Field Generation ──

    def generate_canonical_fields(
        self, bound_data: dict, resolved: ResolvedPolicies
    ) -> CanonicalFieldResult:
        """Generate canonical derived fields + param adjustments.

        Modifies bound_data in place:
          - bound_data["derived_fields"] = {field_id: {key: value, ...}, ...}
          - bound_data["_raw_fields"] = {source_field: {key: raw_value, ...}, ...}
          - bound_data["parameters"][field_id] = same as derived_fields (compiler sync)
        """
        result = CanonicalFieldResult()
        params = bound_data.get("parameters", {})

        if "derived_fields" not in bound_data:
            bound_data["derived_fields"] = {}
        if "_raw_fields" not in bound_data:
            bound_data["_raw_fields"] = {}

        ta = resolved.time_axis

        for field_id, spec in self._derived_field_specs.items():
            derivation = spec.get("derivation", "")
            source = spec.get("source")
            policy_ref = spec.get("policy_ref", "")
            temporal_type = spec.get("temporal_type", "service_day_minute")
            indexed = spec.get("indexed_by") is not None

            if derivation == "cyclic_unwrap" and indexed and isinstance(source, str):
                self._derive_cyclic_unwrap(
                    field_id, source, ta, params, bound_data, result, spec
                )

            elif derivation == "day_offset" and indexed and isinstance(source, str):
                self._derive_day_offset(
                    field_id, source, ta, params, bound_data, result, spec
                )

            elif derivation == "interval_crosses_period" and isinstance(source, list):
                self._derive_interval_crosses(
                    field_id, source, ta, params, bound_data, result, spec
                )

            elif derivation == "window_membership" and indexed:
                self._derive_window_membership(
                    field_id, spec, params, bound_data, result
                )

            else:
                logger.debug(f"Skipping derived field '{field_id}': unsupported derivation '{derivation}'")

        # ── Param canonicalization ──
        for param_id, canon_spec in self._param_canon.items():
            if param_id == "big_m" and canon_spec.get("source") == "time_axis":
                new_big_m = ta.period_minutes * ta.horizon_days
                result.param_adjustments["big_m"] = new_big_m
                logger.info(f"Policy: big_m = {ta.period_minutes} * {ta.horizon_days} = {new_big_m}")

            elif param_id == "trip_duration":
                # Recompute trip_duration if crosses_midnight
                recompute_flag = canon_spec.get("recompute_if")
                if recompute_flag == "crosses_midnight":
                    crosses = bound_data["derived_fields"].get("crosses_midnight", {})
                    dep_raw = bound_data.get("_raw_fields", {}).get("trip_dep_time", {})
                    arr_raw = bound_data.get("_raw_fields", {}).get("trip_arr_time", {})
                    durations = params.get("trip_duration", {})

                    if isinstance(durations, dict) and crosses:
                        recomputed = 0
                        for key, is_cross in crosses.items():
                            if is_cross and key in dep_raw and key in arr_raw:
                                dep_v = dep_raw[key]
                                arr_v = arr_raw[key]
                                new_dur = (arr_v + ta.period_minutes) - dep_v
                                if new_dur > 0:
                                    durations[key] = new_dur
                                    if str(key) in durations:
                                        durations[str(key)] = new_dur
                                    recomputed += 1
                        if recomputed:
                            logger.info(f"Policy: trip_duration recomputed for {recomputed} midnight-crossing trips")

        # Sync derived fields to parameters namespace (compiler access)
        for fid, fvals in bound_data["derived_fields"].items():
            params[fid] = fvals

        return result

    def _derive_cyclic_unwrap(
        self, field_id: str, source: str, ta: TimeAxisPolicy,
        params: dict, bound_data: dict, result: CanonicalFieldResult, spec: dict,
    ):
        """Apply cyclic_unwrap: value < anchor → value + period."""
        source_data = params.get(source, {})
        if not isinstance(source_data, dict):
            return

        # Preserve raw
        if source not in bound_data["_raw_fields"]:
            bound_data["_raw_fields"][source] = copy.copy(source_data)

        canonical = {}
        shifted = 0
        for key, raw_val in source_data.items():
            try:
                v = float(raw_val)
            except (ValueError, TypeError):
                canonical[key] = raw_val
                continue

            new_v = cyclic_unwrap(v, ta.service_day_anchor_minute, ta.period_minutes)
            canonical[key] = int(new_v) if new_v == int(new_v) else new_v

            if new_v != v:
                shifted += 1
                result.provenance.append({
                    "field": field_id,
                    "key": key,
                    "raw": raw_val,
                    "canonical": canonical[key],
                    "policy": "time_axis",
                    "reason": "shift_if_before_anchor",
                })

        bound_data["derived_fields"][field_id] = canonical
        total = len(canonical) // 2  # both int and str keys
        result.fields_created.append(DerivedFieldEntry(
            field_id=field_id, count=total, shifted_count=shifted // 2,
            policy_ref=spec.get("policy_ref", ""), temporal_type=spec.get("temporal_type", ""),
        ))
        logger.info(f"Policy: {field_id} created ({total} values, {shifted // 2} shifted)")

    def _derive_day_offset(
        self, field_id: str, source: str, ta: TimeAxisPolicy,
        params: dict, bound_data: dict, result: CanonicalFieldResult, spec: dict,
    ):
        source_data = params.get(source, {})
        if not isinstance(source_data, dict):
            return

        derived = {}
        for key, raw_val in source_data.items():
            try:
                v = float(raw_val)
            except (ValueError, TypeError):
                derived[key] = 0
                continue
            derived[key] = 0 if v >= ta.service_day_anchor_minute else 1

        bound_data["derived_fields"][field_id] = derived
        result.fields_created.append(DerivedFieldEntry(
            field_id=field_id, count=len(derived) // 2, shifted_count=0,
            policy_ref=spec.get("policy_ref", ""), temporal_type=spec.get("temporal_type", ""),
        ))

    def _derive_interval_crosses(
        self, field_id: str, sources: list, ta: TimeAxisPolicy,
        params: dict, bound_data: dict, result: CanonicalFieldResult, spec: dict,
    ):
        if len(sources) < 2:
            return
        dep_data = params.get(sources[0], {})
        arr_data = params.get(sources[1], {})
        if not isinstance(dep_data, dict) or not isinstance(arr_data, dict):
            return

        derived = {}
        for key in dep_data:
            dep_v = dep_data.get(key)
            arr_v = arr_data.get(key)
            if dep_v is None or arr_v is None:
                derived[key] = False
                continue
            try:
                derived[key] = float(arr_v) < float(dep_v)
            except (ValueError, TypeError):
                derived[key] = False

        bound_data["derived_fields"][field_id] = derived
        cross_count = sum(1 for v in derived.values() if v) // 2
        result.fields_created.append(DerivedFieldEntry(
            field_id=field_id, count=len(derived) // 2, shifted_count=cross_count,
            policy_ref=spec.get("policy_ref", ""), temporal_type=spec.get("temporal_type", ""),
        ))

    def _derive_window_membership(
        self, field_id: str, spec: dict,
        params: dict, bound_data: dict, result: CanonicalFieldResult,
    ):
        source = spec.get("source", "")
        window = spec.get("window", {})
        start = window.get("start", 0)
        end = window.get("end", 1440)

        # Source can be a derived field or a parameter
        if spec.get("source_namespace") == "derived":
            source_data = bound_data.get("derived_fields", {}).get(source, {})
        else:
            source_data = params.get(source, {})

        if not isinstance(source_data, dict):
            return

        derived = {}
        in_count = 0
        for key, val in source_data.items():
            try:
                v = float(val)
                is_member = start <= v <= end
                derived[key] = is_member
                if is_member:
                    in_count += 1
            except (ValueError, TypeError):
                derived[key] = False

        bound_data["derived_fields"][field_id] = derived
        result.fields_created.append(DerivedFieldEntry(
            field_id=field_id, count=len(derived) // 2, shifted_count=in_count // 2,
            policy_ref=spec.get("policy_ref", ""), temporal_type=spec.get("temporal_type", ""),
        ))

    # ── Variable Bound Adjustments ──

    def get_variable_bound_adjustments(self, resolved: ResolvedPolicies) -> dict:
        """Return {var_id: {field: value}} based on time_axis horizon."""
        ta = resolved.time_axis
        adjustments: dict[str, dict] = {}

        for var_id, spec in self._var_bound_specs.items():
            formula = spec.get("upper_bound_formula", "")
            if formula == "period_minutes * horizon_days":
                ub = ta.period_minutes * ta.horizon_days
                adjustments[var_id] = {"upper_bound": ub}

        return adjustments

    # ── Inverse Display ──

    def inverse_display(
        self, field: str, value: float, resolved: ResolvedPolicies
    ) -> tuple[float, Optional[str]]:
        """Inverse transform: canonical → display value.

        Uses inverse_to mapping from derived_field spec.
        Returns (display_value, annotation_or_None).
        """
        ta = resolved.time_axis

        spec = self._derived_field_specs.get(field, {})
        if not spec:
            # Not a derived field, return as-is
            return (value, None)

        derivation = spec.get("derivation", "")

        if derivation == "cyclic_unwrap":
            if value >= ta.period_minutes:
                display_val = value - ta.period_minutes
                h, m = divmod(int(display_val), 60)
                return (display_val, f"익일 {h:02d}:{m:02d}")
            return (value, None)

        return (value, None)

    # ── Temporal Type Queries ──

    def get_temporal_type(self, field_name: str) -> Optional[str]:
        """Look up temporal type for a field."""
        return self._temporal_types.get(field_name)

    def get_temporal_types(self) -> dict[str, str]:
        """Return all temporal type definitions."""
        return dict(self._temporal_types)

    def lint_comparison(self, field_a: str, field_b: str) -> Optional[str]:
        """Check temporal type compatibility between two fields."""
        return lint_temporal_comparison(field_a, field_b, self._temporal_types)
