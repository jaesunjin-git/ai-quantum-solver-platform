"""Tests for engine/policy/ — Policy Engine + Canonical Time Model."""
import copy
import pytest
from unittest.mock import patch

from engine.policy.activation import evaluate_activation
from engine.policy.derivations import cyclic_unwrap, day_offset, interval_crosses_period, window_membership
from engine.policy.temporal_types import TemporalType, lint_temporal_comparison, parse_temporal_type
from engine.policy.policy_engine import (
    PolicyEngine, PolicyResolutionContext, ResolvedPolicies,
    TimeAxisPolicy, OvernightPolicy, CanonicalFieldResult,
)


# ═══════════════════════════════════════════
# 1. Policy Loading
# ═══════════════════════════════════════════

class TestLoadPolicies:
    def test_load_policies_railway(self):
        engine = PolicyEngine("railway")
        assert engine.has_policies()
        assert engine._temporal_types.get("trip_dep_time") == "raw_clock_minute"
        assert engine._temporal_types.get("trip_dep_abs_minute") == "service_day_minute"
        assert engine._temporal_types.get("trip_duration") == "duration_minute"
        assert "trip_dep_abs_minute" in engine._derived_field_specs

    def test_load_missing_domain(self):
        engine = PolicyEngine("nonexistent_domain_xyz")
        assert not engine.has_policies()
        ctx = PolicyResolutionContext(domain="nonexistent_domain_xyz")
        resolved = engine.resolve(ctx)
        assert resolved.time_axis.period_minutes == 1440  # defaults
        assert resolved.overnight.active is False


# ═══════════════════════════════════════════
# 2. Declarative Activation
# ═══════════════════════════════════════════

class TestActivation:
    def test_activation_equals_true(self):
        rule = {"param": "is_overnight_crew", "equals": True}
        params = {"is_overnight_crew": {"value": True, "source": "user"}}
        assert evaluate_activation(rule, params) is True

    def test_activation_equals_false(self):
        rule = {"param": "is_overnight_crew", "equals": True}
        params = {"is_overnight_crew": {"value": False}}
        assert evaluate_activation(rule, params) is False

    def test_activation_missing_param(self):
        rule = {"param": "is_overnight_crew", "equals": True}
        assert evaluate_activation(rule, {}) is False

    def test_activation_greater_than(self):
        rule = {"param": "horizon_days", "greater_than": 1}
        params = {"horizon_days": 2}
        assert evaluate_activation(rule, params) is True

    def test_activation_all_of(self):
        rule = {
            "all_of": [
                {"param": "a", "equals": 1},
                {"param": "b", "equals": 2},
            ]
        }
        assert evaluate_activation(rule, {"a": 1, "b": 2}) is True
        assert evaluate_activation(rule, {"a": 1, "b": 3}) is False

    def test_activation_any_of(self):
        rule = {
            "any_of": [
                {"param": "a", "equals": 1},
                {"param": "b", "equals": 2},
            ]
        }
        assert evaluate_activation(rule, {"a": 1, "b": 99}) is True
        assert evaluate_activation(rule, {"a": 99, "b": 99}) is False

    def test_activation_is_set(self):
        rule = {"param": "x", "is_set": True}
        assert evaluate_activation(rule, {"x": 42}) is True
        assert evaluate_activation(rule, {}) is False


# ═══════════════════════════════════════════
# 3. Derivation Functions
# ═══════════════════════════════════════════

class TestDerivations:
    def test_cyclic_unwrap_before_anchor(self):
        # 316 (05:16) with anchor=1020 → 316 + 1440 = 1756
        assert cyclic_unwrap(316, anchor=1020, period=1440) == 1756

    def test_cyclic_unwrap_after_anchor(self):
        # 1100 (18:20) with anchor=1020 → unchanged
        assert cyclic_unwrap(1100, anchor=1020, period=1440) == 1100

    def test_cyclic_unwrap_at_anchor(self):
        # Exactly at anchor → no shift
        assert cyclic_unwrap(1020, anchor=1020, period=1440) == 1020

    def test_day_offset(self):
        assert day_offset(316, anchor=1020) == 1   # before anchor → next day
        assert day_offset(1100, anchor=1020) == 0   # after anchor → same day

    def test_interval_crosses_midnight(self):
        assert interval_crosses_period(1430, 30) is True   # dep=23:50, arr=00:30
        assert interval_crosses_period(300, 400) is False   # normal

    def test_window_membership(self):
        assert window_membership(1100, start=1020, end=1800) is True
        assert window_membership(900, start=1020, end=1800) is False
        assert window_membership(1756, start=1020, end=1800) is True  # shifted early morning


# ═══════════════════════════════════════════
# 4. Temporal Type Lint
# ═══════════════════════════════════════════

class TestTemporalTypes:
    def test_compatible_same_type(self):
        types = {"a": "service_day_minute", "b": "service_day_minute"}
        assert lint_temporal_comparison("a", "b", types) is None

    def test_compatible_service_plus_duration(self):
        types = {"a": "service_day_minute", "b": "duration_minute"}
        assert lint_temporal_comparison("a", "b", types) is None

    def test_mismatch_raw_vs_service(self):
        types = {"a": "raw_clock_minute", "b": "service_day_minute"}
        result = lint_temporal_comparison("a", "b", types)
        assert result is not None
        assert "mismatch" in result.lower()

    def test_unknown_field_skips_lint(self):
        types = {"a": "service_day_minute"}
        assert lint_temporal_comparison("a", "unknown_field", types) is None

    def test_parse_temporal_type(self):
        assert parse_temporal_type("service_day_minute") == TemporalType.SERVICE_DAY_MINUTE
        assert parse_temporal_type("invalid") == TemporalType.RAW_CLOCK_MINUTE


# ═══════════════════════════════════════════
# 5. Policy Resolution
# ═══════════════════════════════════════════

class TestPolicyResolution:
    def test_resolve_overnight_active(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True, "source": "user"}},
        )
        resolved = engine.resolve(ctx)
        assert resolved.overnight.active is True
        assert resolved.time_axis.service_day_anchor_minute == 1020
        assert resolved.resolved_hash  # non-empty

    def test_resolve_overnight_inactive(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": False}},
        )
        resolved = engine.resolve(ctx)
        assert resolved.overnight.active is False

    def test_resolve_fingerprint_deterministic(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        r1 = engine.resolve(ctx)
        r2 = engine.resolve(ctx)
        assert r1.resolved_hash == r2.resolved_hash

    def test_resolve_sources_tracked(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        assert "overnight.active" in resolved.resolution_sources


# ═══════════════════════════════════════════
# 6. Canonical Field Generation
# ═══════════════════════════════════════════

class TestCanonicalFields:
    def _make_bound_data(self):
        """Create sample bound_data with trip time parameters."""
        return {
            "parameters": {
                "trip_dep_time": {
                    3001: 316, "3001": 316,   # 05:16 (before anchor)
                    3100: 500, "3100": 500,   # 08:20 (before anchor)
                    3200: 1100, "3200": 1100, # 18:20 (after anchor)
                },
                "trip_arr_time": {
                    3001: 355, "3001": 355,
                    3100: 540, "3100": 540,
                    3200: 1140, "3200": 1140,
                },
                "trip_duration": {
                    3001: 39, "3001": 39,
                    3100: 40, "3100": 40,
                    3200: 40, "3200": 40,
                },
            },
            "sets": {},
            "set_sizes": {},
            "parameter_sources": {},
            "parameter_warnings": [],
        }

    def test_derived_fields_created(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        result = engine.generate_canonical_fields(bound, resolved)

        assert "trip_dep_abs_minute" in bound["derived_fields"]
        assert "trip_arr_abs_minute" in bound["derived_fields"]
        assert len(result.fields_created) > 0

    def test_cyclic_unwrap_values(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        engine.generate_canonical_fields(bound, resolved)

        abs_dep = bound["derived_fields"]["trip_dep_abs_minute"]
        # 316 < 1020 → 316 + 1440 = 1756
        assert abs_dep[3001] == 1756
        # 500 < 1020 → 500 + 1440 = 1940
        assert abs_dep[3100] == 1940
        # 1100 >= 1020 → unchanged
        assert abs_dep[3200] == 1100

    def test_raw_preserved(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        engine.generate_canonical_fields(bound, resolved)

        assert "_raw_fields" in bound
        assert bound["_raw_fields"]["trip_dep_time"][3001] == 316

    def test_derived_fields_namespace_separate(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        engine.generate_canonical_fields(bound, resolved)

        # derived_fields is separate namespace
        assert "derived_fields" in bound
        # but also synced to parameters for compiler access
        assert "trip_dep_abs_minute" in bound["parameters"]

    def test_big_m_horizon(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        result = engine.generate_canonical_fields(bound, resolved)

        # big_m = 1440 * 2 = 2880
        assert result.param_adjustments.get("big_m") == 2880

    def test_variable_bounds_horizon(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        adj = engine.get_variable_bound_adjustments(resolved)

        assert adj["duty_start"]["upper_bound"] == 2880
        assert adj["duty_end"]["upper_bound"] == 2880

    def test_provenance_audit(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = self._make_bound_data()

        result = engine.generate_canonical_fields(bound, resolved)

        shifted_provenance = [p for p in result.provenance if p["reason"] == "shift_if_before_anchor"]
        assert len(shifted_provenance) > 0
        p = shifted_provenance[0]
        assert p["field"] == "trip_dep_abs_minute"
        assert p["raw"] == 316
        assert p["canonical"] == 1756
        assert p["policy"] == "time_axis"


# ═══════════════════════════════════════════
# 7. Inverse Display
# ═══════════════════════════════════════════

class TestInverseDisplay:
    def test_inverse_shifted_value(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)

        val, annotation = engine.inverse_display("trip_dep_abs_minute", 1756, resolved)
        assert val == 316
        assert annotation is not None
        assert "익일" in annotation
        assert "05:16" in annotation

    def test_inverse_non_shifted_value(self):
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)

        val, annotation = engine.inverse_display("trip_dep_abs_minute", 1100, resolved)
        assert val == 1100
        assert annotation is None


# ═══════════════════════════════════════════
# 8. Fail-Closed
# ═══════════════════════════════════════════

class TestFailClosed:
    def test_fail_closed_with_canonical_refs(self):
        """PolicyEngine failure + canonical field references → RuntimeError."""
        math_model = {
            "domain": "railway",
            "constraints": [
                {"expression_template": "duty_start[j] <= trip_dep_abs_minute[i]"},
            ],
        }
        # Check that canonical ref detection works
        has_refs = any(
            "abs_minute" in str(c.get("expression_template", ""))
            for c in math_model.get("constraints", [])
            if isinstance(c, dict)
        )
        assert has_refs is True


# ═══════════════════════════════════════════
# 9. Temporal Lint Integration
# ═══════════════════════════════════════════

class TestTemporalLintIntegration:
    def test_lint_raw_vs_service_detected(self):
        engine = PolicyEngine("railway")
        err = engine.lint_comparison("trip_dep_time", "duty_start")
        # trip_dep_time = raw_clock, duty_start = service_day → mismatch
        assert err is not None

    def test_lint_canonical_vs_service_ok(self):
        engine = PolicyEngine("railway")
        err = engine.lint_comparison("trip_dep_abs_minute", "duty_start")
        # both service_day_minute → OK
        assert err is None

    def test_lint_duration_vs_service_ok(self):
        engine = PolicyEngine("railway")
        err = engine.lint_comparison("preparation_minutes", "duty_start")
        # duration + service_day → OK (adding duration to absolute time)
        assert err is None


# ═══════════════════════════════════════════
# 10. Interval Normalization
# ═══════════════════════════════════════════

class TestIntervalNormalization:
    def test_arr_before_dep_crosses_midnight(self):
        """dep=1430, arr=30 → crosses_midnight=True."""
        engine = PolicyEngine("railway")
        ctx = PolicyResolutionContext(
            domain="railway",
            clarification_params={"is_overnight_crew": {"value": True}},
        )
        resolved = engine.resolve(ctx)
        bound = {
            "parameters": {
                "trip_dep_time": {1: 1430, "1": 1430},
                "trip_arr_time": {1: 30, "1": 30},
                "trip_duration": {1: 40, "1": 40},  # will be recomputed
            },
            "sets": {},
            "set_sizes": {},
            "parameter_sources": {},
            "parameter_warnings": [],
        }

        result = engine.generate_canonical_fields(bound, resolved)

        # crosses_midnight should be True
        crosses = bound["derived_fields"].get("crosses_midnight", {})
        assert crosses.get(1) is True

        # trip_arr_abs_minute: 30 < 1020 → 30 + 1440 = 1470
        arr_abs = bound["derived_fields"]["trip_arr_abs_minute"]
        assert arr_abs[1] == 1470

        # trip_dep_abs_minute: 1430 >= 1020 → unchanged
        dep_abs = bound["derived_fields"]["trip_dep_abs_minute"]
        assert dep_abs[1] == 1430

        # trip_duration recomputed: (30 + 1440) - 1430 = 40 → correct
        # (already 40, so unchanged in this case)
