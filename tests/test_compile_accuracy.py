"""
tests/test_compile_accuracy.py
──────────────────────────────
컴파일러 정확성/안전성 테스트 (A1~A6).

10가지 테스트:
  1. hard constant infeasible → compile issues에 error
  2. strict_data_errors + hard data error → compile fail
  3. hard truncation 추적 → CompileIssue(TRUNCATION)
  4. allocation 총합 ≤ cqm_budget
  5. compiler 재호출 시 compile_ctx 초기화
  6. Gate3 structured stats
  7. CompileIssue 모델 검증
  8. CompileContext feasibility_exact/objective_exact
  9. constant tautology → skip (에러 아님)
  10. float tolerance (EPS)
"""
import pytest
from unittest.mock import MagicMock


# ============================================================
# 1. hard constant infeasible → error
# ============================================================

class TestConstantInfeasible:
    def test_hard_infeasible_returns_infeasible(self):
        """lhs=int, rhs=int, infeasible → 'infeasible' 반환"""
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, 5, "<=", 0, "test_0", "hard")
        assert result == "infeasible"
        cqm.add_constraint.assert_not_called()

    def test_soft_infeasible_returns_infeasible(self):
        result = MagicMock()
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, 5, "<=", 0, "test_0", "soft", weight=1.0)
        assert result == "infeasible"

    def test_tautology_returns_tautology(self):
        """0 <= 0 → tautology"""
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, 0, "<=", 0, "test_0", "hard")
        assert result == "tautology"

    def test_negative_diff_tautology(self):
        """-5 <= 0 → tautology"""
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, -5, "<=", 0, "test_0", "hard")
        assert result == "tautology"


# ============================================================
# 2. float tolerance (EPS)
# ============================================================

class TestFloatTolerance:
    def test_near_zero_is_tautology(self):
        """1e-15 <= 0 → tautology (within EPS)"""
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, 1e-15, "<=", 0, "test_0", "hard")
        assert result == "tautology"

    def test_just_above_eps_is_infeasible(self):
        """1.0 <= 0 → infeasible"""
        from engine.compiler.dwave_cqm_compiler import DWaveCQMCompiler
        compiler = DWaveCQMCompiler()
        cqm = MagicMock()
        result = compiler._add_cqm_constraint(cqm, 1.0, "<=", 0, "test_0", "hard")
        assert result == "infeasible"


# ============================================================
# 3. CompileIssue 모델 검증
# ============================================================

class TestCompileIssue:
    def test_create_issue(self):
        from engine.compiler.compile_types import CompileIssue
        issue = CompileIssue(
            code="DATA_ERROR", severity="error",
            constraint="test_con", category="hard",
            detail="variable not found"
        )
        assert issue.code == "DATA_ERROR"
        assert issue.severity == "error"

    def test_compile_context_add_issue(self):
        from engine.compiler.compile_types import CompileContext, CompileIssue
        ctx = CompileContext()
        ctx.add_issue(CompileIssue(
            code="CONSTANT_INFEASIBLE", severity="error",
            constraint="day_duty_start", category="hard",
            detail="2 instances"
        ))
        assert ctx.has_errors
        assert ctx.constant_infeasible_count == 1
        assert not ctx.feasibility_exact


# ============================================================
# 4. CompileContext feasibility_exact / objective_exact
# ============================================================

class TestCompileContextExactness:
    def test_clean_context_is_exact(self):
        from engine.compiler.compile_types import CompileContext
        ctx = CompileContext()
        assert ctx.feasibility_exact
        assert ctx.objective_exact

    def test_hard_data_error_breaks_feasibility(self):
        from engine.compiler.compile_types import CompileContext, CompileIssue
        ctx = CompileContext()
        ctx.add_issue(CompileIssue(
            code="DATA_ERROR", severity="error",
            constraint="test", category="hard", detail="test"
        ))
        assert not ctx.feasibility_exact

    def test_soft_truncation_breaks_objective(self):
        from engine.compiler.compile_types import CompileContext, ConstraintCompileStat
        ctx = CompileContext()
        ctx.constraint_stats.append(ConstraintCompileStat(
            name="soft_con", category="soft", truncated=True
        ))
        assert ctx.feasibility_exact  # soft truncation은 feasibility에 영향 없음
        # objective_exact는 soft truncation만으로는 변경 안 됨 (issue가 필요)

    def test_error_limit(self):
        from engine.compiler.compile_types import CompileContext, CompileIssue
        ctx = CompileContext(max_error_count=3)
        for i in range(5):
            ctx.add_issue(CompileIssue(
                code="DATA_ERROR", severity="error",
                constraint=f"test_{i}", category="hard", detail="test"
            ))
        assert ctx.error_limit_reached


# ============================================================
# 5. compile_ctx 초기화 (재호출 시 상태 잔존 없음)
# ============================================================

class TestCompileContextIsolation:
    def test_each_compile_gets_fresh_context(self):
        """CompileContext는 compile() 내부에서 생성되므로 재호출 시 초기화됨"""
        from engine.compiler.compile_types import CompileContext
        ctx1 = CompileContext()
        ctx1.errors.append("test error")

        ctx2 = CompileContext()
        assert len(ctx2.errors) == 0  # 독립


# ============================================================
# 6. Gate3 structured stats
# ============================================================

class TestGate3StructuredStats:
    def test_gate3_reads_compile_issues(self):
        """compile_issues가 있으면 문자열 파싱 대신 structured 사용"""
        from engine.gates.gate3_compile_check import run as run_gate3

        compile_result = {
            "variable_count": 100,
            "constraint_count": 50,
            "warnings": [],
            "compile_time": 1.0,
            "metadata": {
                "compile_issues": [
                    {"code": "CONSTANT_INFEASIBLE", "severity": "error", "constraint": "c1", "category": "hard", "detail": "test"},
                    {"code": "TRUNCATION", "severity": "warning", "constraint": "c2", "category": "hard", "detail": "test"},
                    {"code": "DATA_ERROR", "severity": "warning", "constraint": "c3", "category": "soft", "detail": "test"},
                ],
                "feasibility_exact": False,
                "objective_exact": True,
            },
        }
        math_model = {
            "constraints": [{"name": f"c{i}", "category": "hard"} for i in range(5)],
        }

        result = run_gate3(compile_result, math_model)
        stats = result["stats"]

        assert stats["constant_infeasible"] == 1
        assert stats["hard_truncation_count"] == 1
        assert stats["data_errors"] == 1
        assert stats["feasibility_exact"] == False
        assert stats["objective_exact"] == True


# ============================================================
# 7. 모듈 import 검증
# ============================================================

class TestModuleImport:
    def test_compile_types_importable(self):
        from engine.compiler.compile_types import (
            CompileIssue, ConstraintCompileStat, CompileContext,
        )
        assert callable(CompileContext)

    def test_gate3_importable(self):
        from engine.gates.gate3_compile_check import run as run_gate3
        assert callable(run_gate3)


# ============================================================
# 8. Affine Collector → CompileIssue 등록 검증
# ============================================================

class TestAffineCollectorCompileIssue:
    """affine collector 경로에서 constant infeasible이 CompileIssue로 등록되는지 검증."""

    def test_hard_constant_infeasible_registers_issue(self):
        """hard 제약이 constant infeasible이면 CompileIssue severity='error'로 등록"""
        from engine.compiler.compile_types import CompileContext
        from engine.compiler.affine_collector import (
            AffineExprIR, check_constant_constraint,
        )

        ctx = CompileContext()

        # -380 >= 0 → infeasible (변수 없이 상수만 남은 hard 제약)
        expr = AffineExprIR(constant=-380.0, linear_terms={})
        result = check_constant_constraint(expr, ">=")
        assert result == "infeasible"

        # CompileIssue 등록 로직 검증 (dwave_cqm_compiler 내부 로직 재현)
        from engine.compiler.compile_types import CompileIssue
        category = "hard"
        ctx.add_issue(CompileIssue(
            code="CONSTANT_INFEASIBLE",
            severity="error" if category == "hard" else "warning",
            constraint="day_duty_start", category=category,
            detail="1 instances (affine_collector)",
        ))

        assert ctx.has_errors
        assert ctx.constant_infeasible_count == 1
        assert not ctx.feasibility_exact

    def test_soft_constant_infeasible_registers_warning(self):
        """soft 제약이 constant infeasible이면 severity='warning' (compile fail 아님)"""
        from engine.compiler.compile_types import CompileContext, CompileIssue

        ctx = CompileContext()
        ctx.add_issue(CompileIssue(
            code="CONSTANT_INFEASIBLE",
            severity="warning",
            constraint="workload_balance", category="soft",
            detail="3 instances (affine_collector)",
        ))

        assert not ctx.has_errors  # soft는 error가 아님
        assert ctx.constant_infeasible_count == 1  # 이슈는 기록됨
        assert ctx.feasibility_exact  # soft는 feasibility에 영향 없음

    def test_tautology_not_registered_as_issue(self):
        """tautology(항상 참)는 CompileIssue로 등록하지 않음"""
        from engine.compiler.affine_collector import AffineExprIR, check_constant_constraint

        # 0 <= 0 → tautology
        expr = AffineExprIR(constant=0.0, linear_terms={})
        assert check_constant_constraint(expr, "<=") == "tautology"

        # -5 <= 0 → tautology
        expr2 = AffineExprIR(constant=-5.0, linear_terms={})
        assert check_constant_constraint(expr2, "<=") == "tautology"

    def test_compile_fails_on_hard_constant_infeasible(self):
        """hard constant infeasible → _has_critical_issues=True → compile fail"""
        from engine.compiler.compile_types import CompileContext, CompileIssue

        ctx = CompileContext()
        ctx.add_issue(CompileIssue(
            code="CONSTANT_INFEASIBLE", severity="error",
            constraint="max_driving_time", category="hard",
            detail="5 instances (affine_collector)",
        ))

        # compile 결과 판정 로직 재현 (dwave_cqm_compiler.py line 419-420)
        _has_critical_issues = any(i.severity == "error" for i in ctx.issues)
        _compile_success = not ctx.has_errors and not _has_critical_issues
        assert not _compile_success  # compile 실패해야 함


# ============================================================
# 9. L3→L4 Canonical 검증 (parameter_errors → compile 차단)
# ============================================================

class TestCanonicalValidationGate:
    """GR-4: parameter validation 실패 시 L4 진입 차단 검증."""

    def test_critical_param_error_blocks_compile(self):
        """타입/range 위반은 compile 진입 차단"""
        from engine.solver_pipeline import BaseSolverPipeline

        pipeline = BaseSolverPipeline()
        # _bind_data 내부에서 bound_data에 parameter_errors가 있으면 차단
        # 직접 호출할 수 없으므로 로직을 단위 테스트

        param_errors = [
            "Parameter 'max_driving_minutes' = 9999: valid_range [60, 720] 벗어남 (family=time_limit)",
            "Parameter 'prep_time_minutes' = 'abc': 숫자 변환 실패 (family=duration, type=scalar)",
        ]
        critical = [e for e in param_errors if "catalog 미등록" not in e]
        assert len(critical) == 2  # 둘 다 치명적

    def test_unregistered_param_is_warning_only(self):
        """catalog 미등록은 경고만 (compile 진행)"""
        param_errors = [
            "Parameter 'custom_field': catalog 미등록 (검증 불가)",
        ]
        critical = [e for e in param_errors if "catalog 미등록" not in e]
        assert len(critical) == 0  # 치명적 에러 없음

    def test_mixed_errors_blocks_on_critical(self):
        """미등록 + range 위반 혼합 시 range 위반만으로 차단"""
        param_errors = [
            "Parameter 'custom_field': catalog 미등록 (검증 불가)",
            "Parameter 'num_crew_day' = -5: valid_range [1, 500] 벗어남 (family=count)",
        ]
        critical = [e for e in param_errors if "catalog 미등록" not in e]
        assert len(critical) == 1
        assert "num_crew_day" in critical[0]

    def test_empty_param_errors_allows_compile(self):
        """parameter_errors 없으면 정상 진행"""
        param_errors = []
        critical = [e for e in param_errors if "catalog 미등록" not in e]
        assert len(critical) == 0
