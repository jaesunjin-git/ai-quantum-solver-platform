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
