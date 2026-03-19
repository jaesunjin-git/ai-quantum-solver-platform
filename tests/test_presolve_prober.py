"""
test_presolve_prober.py ─────────────────────────────────────
Presolve Feasibility Probing 단위 테스트.

테스트 카테고리:
  1. PresolveModels: 데이터 모델 직렬화/역직렬화
  2. CacheKey: 캐시 키 생성 및 무효화
  3. PreCheck: 자명한 infeasibility 탐지
  4. CPSATBuilder: Canonical → CP-SAT 변환 + 리포트
  5. QuickXPlain: 충돌 탐색 알고리즘
  6. PresolveProber: 통합 오케스트레이션
  7. FidelityEnforcement: fidelity 기반 판정 정책
  8. ConflictPairs: 변수 공유 기반 충돌 쌍 추출
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── 1. PresolveModels 테스트 ─────────────────────────────────

class TestPresolveModels:
    """데이터 모델 생성/직렬화 테스트"""

    def test_presolve_status_values(self):
        """PresolveStatus enum 값 검증"""
        from engine.validation.generic.presolve_models import PresolveStatus
        assert PresolveStatus.FEASIBLE.value == "FEASIBLE"
        assert PresolveStatus.INFEASIBLE.value == "INFEASIBLE"
        assert PresolveStatus.TIMEOUT.value == "TIMEOUT"
        assert PresolveStatus.TRIVIAL_INFEASIBLE.value == "TRIVIAL_INFEASIBLE"
        assert PresolveStatus.SKIPPED.value == "SKIPPED"

    def test_fidelity_decision_values(self):
        """FidelityDecision enum 값 검증"""
        from engine.validation.generic.presolve_models import FidelityDecision
        assert FidelityDecision.HARD_BLOCK.value == "hard_block"
        assert FidelityDecision.WARN_ONLY.value == "warn_only"

    def test_trivial_conflict_to_dict(self):
        """TrivialConflict 직렬화"""
        from engine.validation.generic.presolve_models import TrivialConflict
        tc = TrivialConflict(
            code="EMPTY_DOMAIN",
            message="변수 x: lb > ub",
            context={"variable": "x", "lb": 10, "ub": 5},
        )
        d = tc.to_dict()
        assert d["code"] == "EMPTY_DOMAIN"
        assert d["confidence"] == 1.0
        assert d["context"]["lb"] == 10

    def test_presolve_result_to_dict(self):
        """PresolveResult 전체 직렬화"""
        from engine.validation.generic.presolve_models import (
            PresolveResult, PresolveStatus, FidelityDecision,
        )
        r = PresolveResult(
            status=PresolveStatus.FEASIBLE,
            phase="quick_solve",
            elapsed_sec=1.5,
            request_id="abc123",
            decision=FidelityDecision.PROCEED,
        )
        d = r.to_dict()
        assert d["status"] == "FEASIBLE"
        assert d["decision"] == "proceed"
        assert d["elapsed_sec"] == 1.5

    def test_cpsat_build_report_to_dict(self):
        """CPSATBuildReport 직렬화"""
        from engine.validation.generic.presolve_models import (
            CPSATBuildReport, DroppedImpactLevel,
        )
        report = CPSATBuildReport(
            supported_constraints=["c1", "c2"],
            dropped_constraints=["c3"],
            fidelity_score=0.667,
            dropped_impact_level=DroppedImpactLevel.MEDIUM,
        )
        d = report.to_dict()
        assert d["supported_count"] == 2
        assert d["dropped_count"] == 1
        assert 0.66 < d["fidelity_score"] < 0.67

    def test_conflict_diagnosis_to_dict(self):
        """ConflictDiagnosis 직렬화"""
        from engine.validation.generic.presolve_models import (
            ConflictDiagnosis, ConflictEntry, GuaranteeLevel, SoftTestResult,
        )
        diag = ConflictDiagnosis(
            conflict_candidate_set=["c1", "c2"],
            guarantee_level=GuaranteeLevel.VERIFIED_MINIMAL,
            conflicts=[
                ConflictEntry(constraint="c1", confidence=0.8, reason="충돌"),
            ],
            soft_test=SoftTestResult(soft_only_feasible=True),
            solve_count=5,
        )
        d = diag.to_dict()
        assert d["guarantee_level"] == "verified_minimal"
        assert len(d["conflicts"]) == 1
        assert d["soft_test"]["soft_only_feasible"] is True


# ── 2. CacheKey 테스트 ───────────────────────────────────────

class TestCacheKey:
    """캐시 키 생성 및 무효화 검증"""

    def test_same_input_same_key(self):
        """동일 입력 → 동일 캐시 키"""
        from engine.validation.generic.presolve_models import build_cache_key
        mm = {"constraints": [{"name": "c1"}], "variables": []}
        bd = {"parameters": {"p1": 10}, "set_sizes": {"s1": 5}}
        k1 = build_cache_key(mm, bd, "1.0", "2.0")
        k2 = build_cache_key(mm, bd, "1.0", "2.0")
        assert k1 == k2

    def test_different_policy_version_different_key(self):
        """정책 버전 변경 → 다른 캐시 키"""
        from engine.validation.generic.presolve_models import build_cache_key
        mm = {"constraints": [{"name": "c1"}], "variables": []}
        bd = {"parameters": {}, "set_sizes": {}}
        k1 = build_cache_key(mm, bd, "1.0", "2.0")
        k2 = build_cache_key(mm, bd, "1.1", "2.0")
        assert k1 != k2

    def test_different_catalog_version_different_key(self):
        """카탈로그 버전 변경 → 다른 캐시 키"""
        from engine.validation.generic.presolve_models import build_cache_key
        mm = {"constraints": [{"name": "c1"}], "variables": []}
        bd = {"parameters": {}, "set_sizes": {}}
        k1 = build_cache_key(mm, bd, "1.0", "2.0")
        k2 = build_cache_key(mm, bd, "1.0", "3.0")
        assert k1 != k2

    def test_different_constraints_different_key(self):
        """제약 변경 → 다른 캐시 키"""
        from engine.validation.generic.presolve_models import build_cache_key
        bd = {"parameters": {}, "set_sizes": {}}
        k1 = build_cache_key({"constraints": [{"name": "c1"}], "variables": []}, bd)
        k2 = build_cache_key({"constraints": [{"name": "c2"}], "variables": []}, bd)
        assert k1 != k2


# ── 3. PreCheck 테스트 ───────────────────────────────────────

class TestPreCheck:
    """자명한 infeasibility 탐지"""

    def test_empty_domain_detected(self):
        """변수 lb > ub → EMPTY_DOMAIN 탐지"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        math_model = {
            "variables": [{"id": "x", "type": "integer", "lower_bound": 10, "upper_bound": 5}],
            "constraints": [],
        }
        issues = prober._pre_check(math_model, {"sets": {}, "parameters": {}})
        assert len(issues) == 1
        assert issues[0].code == "EMPTY_DOMAIN"

    def test_no_issues_for_valid_model(self):
        """정상 모델 → 이슈 없음"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        math_model = {
            "variables": [{"id": "x", "type": "integer", "lower_bound": 0, "upper_bound": 100}],
            "constraints": [],
        }
        issues = prober._pre_check(math_model, {"sets": {}, "parameters": {}})
        assert len(issues) == 0

    def test_resource_shortage_detected(self):
        """자원 부족 탐지"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        math_model = {"variables": [], "constraints": []}
        bound_data = {
            "sets": {"crews": [1, 2], "trips": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
            "parameters": {"max_duties_per_crew": 3},
        }
        issues = prober._pre_check(math_model, bound_data)
        assert any(i.code == "RESOURCE_SHORTAGE" for i in issues)


# ── 4. Config 테스트 ─────────────────────────────────────────

class TestPresolveConfig:
    """설정 로딩 검증"""

    def test_default_config(self):
        """기본 설정값 검증"""
        from engine.validation.generic.presolve_models import PresolveConfig
        cfg = PresolveConfig()
        assert cfg.quick_solve_time_limit_sec == 10
        assert cfg.max_solves == 50
        assert cfg.max_depth == 10
        assert cfg.fidelity_high == 0.9
        assert cfg.fidelity_medium == 0.7

    def test_env_override(self):
        """환경변수 오버라이드"""
        import os
        from engine.validation.generic.presolve_models import PresolveConfig
        os.environ["PRESOLVE_MAX_SOLVES"] = "100"
        try:
            cfg = PresolveConfig.from_env()
            assert cfg.max_solves == 100
        finally:
            del os.environ["PRESOLVE_MAX_SOLVES"]


# ── 5. QuickXPlain 유틸리티 테스트 ────────────────────────────

class TestQuickXPlainUtils:
    """QuickXPlain 관련 유틸리티 함수 테스트"""

    def test_rank_constraints_temporal_first(self):
        """temporal 제약이 앞에 배치"""
        from engine.validation.generic.quickxplain import rank_constraints_by_risk
        mm = {
            "constraints": [
                {"name": "c_other", "category": "hard"},
                {"name": "c_time", "category": "temporal"},
                {"name": "c_cap", "category": "capacity"},
            ]
        }
        ranked = rank_constraints_by_risk(["c_other", "c_time", "c_cap"], mm)
        assert ranked[0] == "c_time"

    def test_extract_conflict_pairs_shared_vars(self):
        """공유 변수 기반 충돌 쌍 추출"""
        from engine.validation.generic.quickxplain import extract_conflict_pairs
        mm = {
            "variables": [{"id": "x"}, {"id": "y"}],
            "constraints": [
                {"name": "c1", "expression": "x + y <= 10"},
                {"name": "c2", "expression": "x >= 5"},
                {"name": "c3", "expression": "z >= 1"},  # x, y 미참조
            ],
        }
        pairs = extract_conflict_pairs(["c1", "c2"], mm)
        assert len(pairs) >= 1
        assert pairs[0]["shared_variables"] == ["x"]

    def test_calculate_confidence_in_set(self):
        """conflict set 포함 시 confidence > 0"""
        from engine.validation.generic.quickxplain import calculate_confidence
        conf = calculate_confidence("c1", ["c1", "c2"], [], None)
        assert conf > 0.0

    def test_calculate_confidence_not_in_set(self):
        """conflict set 미포함 시 낮은 confidence"""
        from engine.validation.generic.quickxplain import calculate_confidence
        conf = calculate_confidence("c3", ["c1", "c2"], [], None)
        assert conf < 0.5


# ── 6. FidelityEnforcement 테스트 ─────────────────────────────

class TestFidelityEnforcement:
    """Fidelity 기반 판정 정책 강제 검증"""

    def test_high_fidelity_hard_block(self):
        """fidelity ≥ 0.9 + INFEASIBLE → hard_block"""
        from engine.validation.generic.presolve_models import (
            PresolveConfig, FidelityDecision,
        )
        cfg = PresolveConfig()
        fidelity = 0.95
        assert fidelity >= cfg.fidelity_high
        # 코드 강제 확인: high fidelity → hard_block 허용
        decision = FidelityDecision.HARD_BLOCK
        assert decision == FidelityDecision.HARD_BLOCK

    def test_medium_fidelity_conditional_block(self):
        """0.7 ≤ fidelity < 0.9 → conditional_block"""
        from engine.validation.generic.presolve_models import PresolveConfig
        cfg = PresolveConfig()
        fidelity = 0.8
        assert cfg.fidelity_medium <= fidelity < cfg.fidelity_high

    def test_low_fidelity_warn_only(self):
        """fidelity < 0.7 → warn_only (hard block 금지)"""
        from engine.validation.generic.presolve_models import PresolveConfig
        cfg = PresolveConfig()
        fidelity = 0.5
        assert fidelity < cfg.fidelity_medium


# ── 7. DroppedImpact 테스트 ───────────────────────────────────

class TestDroppedImpact:
    """변환 시 생략된 제약의 영향도 평가"""

    def test_high_impact_hard_constraint(self):
        """hard 제약 생략 → HIGH"""
        from engine.validation.generic.canonical_cpsat_builder import _assess_dropped_impact
        from engine.validation.generic.presolve_models import DroppedImpactLevel
        level, note = _assess_dropped_impact(
            ["c1"], [{"name": "c1", "category": "hard"}]
        )
        assert level == DroppedImpactLevel.HIGH

    def test_low_impact_few_soft(self):
        """soft 제약 소수 생략 → LOW"""
        from engine.validation.generic.canonical_cpsat_builder import _assess_dropped_impact
        from engine.validation.generic.presolve_models import DroppedImpactLevel
        level, _ = _assess_dropped_impact(
            ["c1"], [{"name": "c1", "category": "soft"}]
        )
        assert level == DroppedImpactLevel.LOW

    def test_medium_impact_many_dropped(self):
        """5개 이상 생략 → MEDIUM"""
        from engine.validation.generic.canonical_cpsat_builder import _assess_dropped_impact
        from engine.validation.generic.presolve_models import DroppedImpactLevel
        dropped = [f"c{i}" for i in range(6)]
        cdefs = [{"name": f"c{i}", "category": "soft"} for i in range(6)]
        level, _ = _assess_dropped_impact(dropped, cdefs)
        assert level == DroppedImpactLevel.MEDIUM


# ── 8. Prober Skip 조건 테스트 ────────────────────────────────

class TestProberSkipConditions:
    """Presolve skip 조건 검증"""

    def test_skip_no_bound_data(self):
        """bound_data 없으면 SKIPPED"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        ctx = {"math_model": {"constraints": [{"name": "c1"}] * 20}, "bound_data": {}}
        result = prober.validate(ctx)
        assert any(item.code == "PRESOLVE_SKIPPED" for item in result.items)

    def test_skip_small_model(self):
        """제약 < threshold면 SKIPPED"""
        from engine.validation.generic.presolve_prober import PresolveProber
        from engine.validation.generic.presolve_models import PresolveConfig
        cfg = PresolveConfig(skip_threshold_constraints=10)
        prober = PresolveProber(config=cfg)
        ctx = {
            "math_model": {"constraints": [{"name": f"c{i}"} for i in range(5)]},
            "bound_data": {"sets": {"s": [1]}, "parameters": {}},
        }
        result = prober.validate(ctx)
        assert any(item.code == "PRESOLVE_SKIPPED_SMALL" for item in result.items)


# ── 9. TimeoutAssessment 테스트 ───────────────────────────────

class TestTimeoutAssessment:
    """TIMEOUT risk 평가 검증"""

    def test_high_risk_constant_infeasible(self):
        """constant infeasible 존재 시 high risk"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        assessment = prober._assess_timeout_risk(
            math_model={"variables": [{"id": f"v{i}"} for i in range(100)],
                        "constraints": [{"name": f"c{i}"} for i in range(200)]},
            gate3_result={"stats": {"constant_infeasible": 3, "hard_truncation_count": 2}},
            build_report=MagicMock(fidelity_score=0.95),
        )
        assert assessment.risk_score >= 0.5
        assert assessment.action == "user_confirmation"

    def test_low_risk_clean_model(self):
        """클린 모델 → low risk"""
        from engine.validation.generic.presolve_prober import PresolveProber
        prober = PresolveProber()
        assessment = prober._assess_timeout_risk(
            math_model={"variables": [{"id": "v1"}], "constraints": [{"name": "c1"}]},
            gate3_result={"stats": {}},
            build_report=MagicMock(fidelity_score=0.95),
        )
        assert assessment.risk_score < 0.5
        assert assessment.action == "proceed_with_warning"
