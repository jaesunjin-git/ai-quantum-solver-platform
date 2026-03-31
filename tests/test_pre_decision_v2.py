"""
tests/test_pre_decision_v2.py
──────────────────────────────
Pre-Decision Engine v2.0 테스트:
- Problem Profile 고도화 (문제 클래스 확장, 구조 분석)
- model_type 매칭 및 NL 네이티브 보너스
- Scale 점수 가우시안 커브
- exact vs approximate 재조정
- 제약조건 복잡도 보너스/페널티
- data_facts 기반 변수 수 보정
- DB 솔버 시딩
"""
import pytest
import math


# ============================================================
# Helper: 테스트용 수학 모델 생성
# ============================================================

def _make_math_model(
    problem_name="crew_scheduling",
    domain="railway",
    variables=None,
    constraints=None,
    sets=None,
    objective=None,
    metadata=None,
):
    if variables is None:
        variables = [
            {"id": "x_ij", "type": "binary", "indices": ["I", "J"]},
        ]
    if constraints is None:
        constraints = [
            {"name": "C1", "category": "hard", "expression": "sum(x[i,j] for j in J) == 1",
             "description": "모든 운행 배정"},
            {"name": "C2", "category": "hard", "expression": "sum(x[i,j]) <= capacity",
             "description": "용량 제약"},
            {"name": "S1", "category": "soft", "expression": "balance >= 0",
             "description": "균형 제약", "weight": 0.5},
        ]
    if sets is None:
        sets = [
            {"id": "I", "source_column": "trip_id", "source_file": "trips.csv", "elements": list(range(100))},
            {"id": "J", "source_column": "duty_id", "source_file": "duties.csv", "elements": list(range(30))},
        ]
    if objective is None:
        objective = {"type": "minimize", "expression": "sum(cost[i,j] * x[i,j])", "description": "총 비용 최소화"}
    if metadata is None:
        metadata = {"estimated_variable_count": 3000, "estimated_constraint_count": 150}

    return {
        "problem_name": problem_name,
        "domain": domain,
        "variables": variables,
        "constraints": constraints,
        "sets": sets,
        "objective": objective,
        "metadata": metadata,
    }


def _make_data_facts(unique_counts=None):
    if unique_counts is None:
        unique_counts = {
            "trips.csv.trip_id": 120,
            "duties.csv.duty_id": 40,
        }
    return {
        "files": [
            {"name": "trips.csv", "records": 120, "columns": ["trip_id"]},
            {"name": "duties.csv", "records": 40, "columns": ["duty_id"]},
        ],
        "unique_counts": unique_counts,
    }


# ============================================================
# 1. Problem Profile 고도화
# ============================================================

class TestProblemProfile:
    """Phase 1-B: Problem Profile 분류 정확도"""

    def test_scheduling_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="crew_scheduling_3호선")
        profile = build_problem_profile(model)
        assert "scheduling" in profile["problem_classes"]

    def test_routing_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="vehicle_routing_problem")
        profile = build_problem_profile(model)
        assert "routing" in profile["problem_classes"]

    def test_permutation_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="crew_permutation_optimization")
        profile = build_problem_profile(model)
        assert "permutation" in profile["problem_classes"]

    def test_subset_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="subset_selection_problem")
        profile = build_problem_profile(model)
        assert "subset_selection" in profile["problem_classes"]

    def test_tsp_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="TSP_delivery_route")
        profile = build_problem_profile(model)
        assert "TSP" in profile["problem_classes"]

    def test_knapsack_keyword(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(problem_name="knapsack_packing")
        profile = build_problem_profile(model)
        assert "knapsack" in profile["problem_classes"]

    def test_constraint_structure_permutation(self):
        """제약조건에 all_different → permutation 분류"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(
            problem_name="generic_problem",
            constraints=[
                {"name": "C1", "category": "hard",
                 "expression": "all_different(x[j] for j in J)",
                 "description": "각 근무는 서로 다른 승무원"},
            ],
        )
        profile = build_problem_profile(model)
        assert "permutation" in profile["problem_classes"]

    def test_multi_index_binary_infers_assignment(self):
        """2-index binary 변수 → assignment 추론"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(
            problem_name="generic_optimization",
            variables=[{"id": "x_ij", "type": "binary", "indices": ["I", "J"]}],
            constraints=[],
        )
        profile = build_problem_profile(model)
        assert "assignment" in profile["problem_classes"]

    def test_constraint_features_extracted(self):
        """제약조건 구조 분석 필드 존재"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model()
        profile = build_problem_profile(model)
        assert "constraint_features" in profile
        assert isinstance(profile["constraint_features"]["has_permutation"], bool)
        assert isinstance(profile["constraint_features"]["has_nonlinear"], bool)

    def test_nonlinear_objective_detected(self):
        """비선형 목적함수 감지"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(
            objective={"type": "minimize", "expression": "sum(x[i]*y[i])"}
        )
        profile = build_problem_profile(model)
        assert profile["is_nonlinear_objective"] is True


# ============================================================
# 2. data_facts 기반 변수 수 보정
# ============================================================

class TestDataFactsIntegration:
    """Phase 3-B: data_facts로 변수 수 실측"""

    def test_data_facts_overrides_metadata(self):
        """data_facts의 unique_counts로 실제 변수 수 계산"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(metadata={"estimated_variable_count": 3000})
        data_facts = _make_data_facts({"trips.csv.trip_id": 120, "duties.csv.duty_id": 40})
        profile = build_problem_profile(model, data_facts=data_facts)
        # x_ij: I(120) × J(40) = 4800
        assert profile["variable_count"] == 4800
        assert profile["variable_count_source"] == "calculated"

    def test_data_facts_flag_set(self):
        from engine.solver_registry import build_problem_profile
        model = _make_math_model()
        data_facts = _make_data_facts()
        profile = build_problem_profile(model, data_facts=data_facts)
        assert profile["data_facts_available"] is True

    def test_no_data_facts_uses_set_elements(self):
        """data_facts 없으면 set elements로 계산"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model()  # sets have 100 and 30 elements
        profile = build_problem_profile(model, data_facts=None)
        assert profile["variable_count"] == 3000  # 100 * 30
        assert profile["variable_count_source"] == "calculated"

    def test_no_data_no_sets_uses_llm(self):
        """sets/data_facts 모두 없으면 LLM 추정 사용"""
        from engine.solver_registry import build_problem_profile
        model = _make_math_model(
            sets=[{"id": "I"}, {"id": "J"}],  # no elements
            metadata={"estimated_variable_count": 5000},
        )
        profile = build_problem_profile(model, data_facts=None)
        assert profile["variable_count"] == 5000
        assert profile["variable_count_source"] == "llm_estimate"


# ============================================================
# 3. Scoring Engine v2.0
# ============================================================

class TestScoringV2:
    """Phase 1-C + 2-A~D: score_solver 개선"""

    def _get_solver(self, solver_id):
        from engine.solver_registry import SolverRegistry
        SolverRegistry.reload()
        return SolverRegistry.get_solver(solver_id)

    def _make_scheduling_profile(self, var_count=3000):
        return {
            "variable_count": var_count,
            "constraint_count": 150,
            "variable_types": ["binary", "integer"],
            "has_constraints": True,
            "hard_constraint_count": 10,
            "soft_constraint_count": 5,
            "problem_classes": ["scheduling"],
            "constraint_features": {"has_permutation": False, "has_nonlinear": False,
                                    "has_conditional": False, "total_count": 15},
            "is_nonlinear_objective": False,
            "has_multi_index_binary": True,
            "data_facts_available": False,
        }

    def _make_permutation_profile(self, var_count=3000):
        return {
            "variable_count": var_count,
            "constraint_count": 150,
            "variable_types": ["binary"],
            "has_constraints": True,
            "hard_constraint_count": 10,
            "soft_constraint_count": 5,
            "problem_classes": ["scheduling", "permutation"],
            "constraint_features": {"has_permutation": True, "has_nonlinear": False,
                                    "has_conditional": False, "total_count": 15},
            "is_nonlinear_objective": False,
            "has_multi_index_binary": True,
            "data_facts_available": False,
        }

    def test_nl_gets_permutation_bonus(self):
        """NL 솔버: permutation 문제에서 네이티브 보너스"""
        from engine.solver_registry import score_solver
        nl = self._get_solver("dwave_nl")
        assert nl is not None, "dwave_nl not found in YAML"

        profile = self._make_permutation_profile()
        result = score_solver(nl, profile)
        assert any("네이티브" in r for r in result["reasons"])
        assert result["scores"]["structure"] >= 80

    def test_classical_no_native_bonus(self):
        """Classical CPU: permutation 문제에서 네이티브 보너스 없음"""
        from engine.solver_registry import score_solver
        cpu = self._get_solver("classical_cpu")
        profile = self._make_permutation_profile()
        result = score_solver(cpu, profile)
        assert not any("네이티브 지원: " in r and "permutation" in r for r in result["reasons"])

    def test_nl_higher_than_cqm_for_permutation(self):
        """permutation 문제에서 NL > CQM 점수"""
        from engine.solver_registry import score_solver
        nl = self._get_solver("dwave_nl")
        cqm = self._get_solver("dwave_hybrid_cqm")
        profile = self._make_permutation_profile()

        nl_score = score_solver(nl, profile)["scores"]["structure"]
        cqm_score = score_solver(cqm, profile)["scores"]["structure"]
        assert nl_score > cqm_score, f"NL({nl_score}) should > CQM({cqm_score})"

    def test_scale_gaussian_sweet_spot(self):
        """Scale 점수: sweet spot (1~30% utilization)에서 높은 점수"""
        from engine.solver_registry import score_solver
        nl = self._get_solver("dwave_nl")  # max_variables: 2000000

        # sweet spot: 20,000 vars / 2M max = 1% → 높은 점수
        profile = self._make_scheduling_profile(var_count=20000)
        result = score_solver(nl, profile)
        assert result["scores"]["scale"] >= 70, f"Scale should be ≥70 at sweet spot, got {result['scores']['scale']}"

    def test_scale_very_small_problem(self):
        """Scale 점수: 매우 작은 문제는 낮은 점수"""
        from engine.solver_registry import score_solver
        nl = self._get_solver("dwave_nl")  # max_variables: 2000000

        # 10 vars / 2M max = 0.000005 → 낮은 점수
        profile = self._make_scheduling_profile(var_count=10)
        result = score_solver(nl, profile)
        assert result["scores"]["scale"] < 50

    def test_scale_exceeds_capacity(self):
        """Scale 점수: 용량 초과 → 0점"""
        from engine.solver_registry import score_solver
        cpu = self._get_solver("classical_cpu")  # max_variables: 10M

        profile = self._make_scheduling_profile(var_count=20_000_000)
        result = score_solver(cpu, profile)
        assert result["scores"]["scale"] == 0

    def test_exact_no_bonus_for_large_problems(self):
        """대규모 문제: exact 보너스 제거"""
        from engine.solver_registry import score_solver
        cpu = self._get_solver("classical_cpu")  # exact guarantee

        # 소규모: +5 보너스
        small_profile = self._make_scheduling_profile(var_count=500)
        small_result = score_solver(cpu, small_profile)

        # 대규모: 보너스 없음
        large_profile = self._make_scheduling_profile(var_count=50000)
        large_result = score_solver(cpu, large_profile)

        assert any("최적해 보장" in r and "대규모" not in r for r in small_result["reasons"])
        assert any("대규모" in r for r in large_result["reasons"])

    def test_many_constraints_bonus(self):
        """제약조건 많은 문제: constraint-native 솔버 보너스"""
        from engine.solver_registry import score_solver
        cqm = self._get_solver("dwave_hybrid_cqm")  # supports_constraints: true

        profile = self._make_scheduling_profile()
        profile["hard_constraint_count"] = 25
        profile["soft_constraint_count"] = 10
        result = score_solver(cqm, profile)
        assert any("다수 제약조건" in r for r in result["reasons"])

    def test_bqm_penalty_for_many_hard_constraints(self):
        """BQM: 하드 제약 20개 이상 → 페널티"""
        from engine.solver_registry import score_solver
        bqm = self._get_solver("dwave_hybrid_bqm")
        if bqm is None:
            pytest.skip("dwave_hybrid_bqm not in YAML")

        profile = self._make_scheduling_profile()
        profile["hard_constraint_count"] = 25
        result = score_solver(bqm, profile)
        assert any("페널티 변환 비효율" in w for w in result["warnings"])


# ============================================================
# 4. recommend_solvers 통합
# ============================================================

class TestRecommendSolversV2:
    """recommend_solvers v2.0 통합 테스트"""

    def test_recommend_returns_profile_with_new_fields(self):
        from engine.solver_registry import recommend_solvers
        model = _make_math_model()
        result = recommend_solvers(model)
        profile = result["problem_profile"]
        assert "constraint_features" in profile
        assert "is_nonlinear_objective" in profile
        assert "variable_count_source" in profile

    def test_recommend_with_data_facts(self):
        from engine.solver_registry import recommend_solvers
        model = _make_math_model()
        data_facts = _make_data_facts()
        result = recommend_solvers(model, data_facts=data_facts)
        profile = result["problem_profile"]
        assert profile["data_facts_available"] is True
        # data_facts로 보정된 변수 수: 120*40 = 4800
        assert profile["variable_count"] == 4800

    def test_recommend_weights_used_field(self):
        from engine.solver_registry import recommend_solvers
        model = _make_math_model()
        result = recommend_solvers(model, priority="accuracy")
        assert "weights_used" in result
        assert result["weights_used"]["structure"] == 0.50

    def test_dynamic_weights_large_problem(self):
        """대규모 문제: auto 모드에서 scale 가중치 증가"""
        from engine.solver_registry import _get_dynamic_weights
        large_profile = {"variable_count": 200000}
        weights = _get_dynamic_weights("auto", large_profile)
        assert weights["scale"] >= 0.35

    def test_dynamic_weights_small_problem(self):
        """소규모 문제: auto 모드에서 structure 가중치 증가"""
        from engine.solver_registry import _get_dynamic_weights
        small_profile = {"variable_count": 50}
        weights = _get_dynamic_weights("auto", small_profile)
        assert weights["structure"] >= 0.50

    def test_dynamic_weights_explicit_priority_unchanged(self):
        """명시적 priority는 동적 조정 안 함"""
        from engine.solver_registry import _get_dynamic_weights, DEFAULT_WEIGHTS
        profile = {"variable_count": 200000}
        weights = _get_dynamic_weights("accuracy", profile)
        assert weights == DEFAULT_WEIGHTS["accuracy"]

    def test_nl_path_penalty_for_crew_scheduling(self):
        """crew scheduling에서 NL은 IR 경로 패널티로 SP solver보다 낮은 순위"""
        from engine.solver_registry import recommend_solvers
        model = _make_math_model(
            problem_name="crew_scheduling_permutation",
            constraints=[
                {"name": "C1", "category": "hard",
                 "expression": "all_different(x[j])",
                 "description": "순열 제약"},
            ] + [
                {"name": f"H{i}", "category": "hard",
                 "expression": f"sum(x[i,j]) <= {i}",
                 "description": f"제약{i}"}
                for i in range(15)
            ] + [
                {"name": f"S{i}", "category": "soft",
                 "expression": f"balance_{i} >= 0",
                 "description": f"소프트{i}"}
                for i in range(5)
            ],
            metadata={"estimated_variable_count": 5000, "estimated_constraint_count": 200},
        )
        model["domain"] = "railway"  # crew_scheduling problem type
        result = recommend_solvers(model)
        recs = result["recommendations"]

        # SP 가능 solver (classical_cpu, CQM)가 NL보다 높은 순위
        nl_rec = next((r for r in recs if r["solver_id"] == "dwave_nl"), None)
        cpsat_rec = next((r for r in recs if r["solver_id"] == "classical_cpu"), None)
        if nl_rec and cpsat_rec:
            assert cpsat_rec["total_score"] > nl_rec["total_score"], \
                f"CP-SAT ({cpsat_rec['total_score']}) should rank higher than NL ({nl_rec['total_score']}) for crew scheduling"
        # NL에 IR 패널티 reason이 포함되어 있는지
        if nl_rec:
            reasons = " ".join(nl_rec.get("reasons", []))
            assert "IR" in reasons or "패널티" in reasons, f"NL should have IR penalty reason: {nl_rec.get('reasons', [])}"


# ============================================================
# 5. DB 솔버 시딩
# ============================================================

class TestSolverSeeding:
    """Phase 1-A: main.py startup 솔버 시딩"""

    def test_seeding_code_exists(self):
        import inspect
        from main import _startup as startup
        source = inspect.getsource(startup)
        assert "SolverSettingDB" in source
        assert "dwave_nl" in source
        assert "classical_cpu" in source

    def test_default_enabled_solvers(self):
        """기본 활성 솔버: classical_cpu, dwave_hybrid_cqm, dwave_nl"""
        import inspect
        from main import _startup as startup
        source = inspect.getsource(startup)
        # 시딩 코드에서 enabled=True인 솔버 확인
        assert '"classical_cpu", "enabled": True' in source or \
               "'classical_cpu', 'enabled': True" in source or \
               '"solver_id": "classical_cpu", "enabled": True' in source


# ============================================================
# 6. 유틸리티 함수
# ============================================================

class TestUtilities:
    """_parse_int, _resolve_set_size 등"""

    def test_parse_int_string(self):
        from engine.solver_registry import _parse_int
        assert _parse_int("3,000") == 3000
        assert _parse_int("500") == 500
        assert _parse_int("abc") == 0

    def test_parse_int_numeric(self):
        from engine.solver_registry import _parse_int
        assert _parse_int(42) == 42
        assert _parse_int(3.7) == 3

    def test_resolve_set_size_from_data_facts(self):
        from engine.solver_registry import _resolve_set_size
        set_def = {"id": "I", "source_column": "trip_id", "source_file": "trips.csv"}
        data_facts = {"unique_counts": {"trips.csv.trip_id": 120}}
        assert _resolve_set_size(set_def, data_facts) == 120

    def test_resolve_set_size_from_elements(self):
        from engine.solver_registry import _resolve_set_size
        set_def = {"id": "I", "elements": [1, 2, 3, 4, 5]}
        assert _resolve_set_size(set_def, None) == 5

    def test_resolve_set_size_no_info(self):
        from engine.solver_registry import _resolve_set_size
        set_def = {"id": "I"}
        assert _resolve_set_size(set_def, None) == 0

    def test_classify_suitability(self):
        from engine.solver_registry import _classify_suitability
        assert _classify_suitability(85) == "Best Choice"
        assert _classify_suitability(70) == "Recommended"
        assert _classify_suitability(55) == "Possible"
        assert _classify_suitability(40) == "Limited"
        assert _classify_suitability(20) == "Not Suitable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
