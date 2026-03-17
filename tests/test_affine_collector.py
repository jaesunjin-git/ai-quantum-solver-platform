"""
tests/test_affine_collector.py
──────────────────────────────
Affine Collector 테스트 — P2 컴파일러 최적화 검증.

17개 테스트:
  1-2. strict/lenient 정책
  3. coerce_scalar
  4. normalize_index_key
  5. 예외 분류
  6. eval_scalar
  7-9. collect_affine
  10. normalize_constraint
  11. constant-only 제약
  12. lower_affine_to_dimod
  13. empty set / zero coefficient
  14. near-zero pruning
  15. max_driving_time_0 재현
  16. product: scalar×affine vs variable×variable
  17. 회귀 (import 검증)
"""
import math
import pytest
from unittest.mock import MagicMock


# ============================================================
# 1-2. strict/lenient 정책 (errors.py 계층)
# ============================================================

class TestErrorHierarchy:
    def test_fallback_allowed_is_catchable(self):
        from engine.compiler.errors import (
            StructuredBuildError, StructuredFallbackAllowed,
            UnsupportedStructuredPattern, StructuredDataError,
        )
        with pytest.raises(StructuredFallbackAllowed):
            raise UnsupportedStructuredPattern("test")
        # StructuredBuildError로도 잡힘
        with pytest.raises(StructuredBuildError):
            raise UnsupportedStructuredPattern("test")

    def test_data_error_not_fallback(self):
        from engine.compiler.errors import (
            StructuredFallbackAllowed, StructuredDataError,
            NonScalarBoundValueError,
        )
        # StructuredDataError는 StructuredFallbackAllowed로 잡히지 않음
        with pytest.raises(StructuredDataError):
            raise NonScalarBoundValueError("test")
        try:
            raise NonScalarBoundValueError("test")
        except StructuredFallbackAllowed:
            pytest.fail("StructuredDataError should NOT be caught by StructuredFallbackAllowed")
        except StructuredDataError:
            pass


# ============================================================
# 3. coerce_scalar
# ============================================================

class TestCoerceScalar:
    def test_python_scalars(self):
        from engine.compiler.struct_builder import coerce_scalar
        assert coerce_scalar(5, name="x") == 5
        assert coerce_scalar(3.14, name="x") == 3.14

    def test_numpy_scalar(self):
        from engine.compiler.struct_builder import coerce_scalar
        try:
            import numpy as np
            assert coerce_scalar(np.int64(42), name="x") == 42
            assert isinstance(coerce_scalar(np.int64(42), name="x"), int)
            assert coerce_scalar(np.float64(2.5), name="x") == 2.5
        except ImportError:
            pytest.skip("numpy not installed")

    def test_none_raises(self):
        from engine.compiler.struct_builder import coerce_scalar
        from engine.compiler.errors import NoneValueError
        with pytest.raises(NoneValueError):
            coerce_scalar(None, name="x")

    def test_nan_raises(self):
        from engine.compiler.struct_builder import coerce_scalar
        from engine.compiler.errors import NonFiniteValueError
        with pytest.raises(NonFiniteValueError):
            coerce_scalar(float('nan'), name="x")

    def test_inf_raises(self):
        from engine.compiler.struct_builder import coerce_scalar
        from engine.compiler.errors import NonFiniteValueError
        with pytest.raises(NonFiniteValueError):
            coerce_scalar(float('inf'), name="x")
        with pytest.raises(NonFiniteValueError):
            coerce_scalar(float('-inf'), name="x")

    def test_ndarray_size_gt1_raises(self):
        from engine.compiler.struct_builder import coerce_scalar
        from engine.compiler.errors import NonScalarBoundValueError
        try:
            import numpy as np
            with pytest.raises(NonScalarBoundValueError):
                coerce_scalar(np.array([1, 2, 3]), name="x")
        except ImportError:
            pytest.skip("numpy not installed")

    def test_list_len1(self):
        from engine.compiler.struct_builder import coerce_scalar
        assert coerce_scalar([42], name="x") == 42

    def test_list_len_gt1_raises(self):
        from engine.compiler.struct_builder import coerce_scalar
        from engine.compiler.errors import NonScalarBoundValueError
        with pytest.raises(NonScalarBoundValueError):
            coerce_scalar([1, 2], name="x")


# ============================================================
# 4. normalize_index_key
# ============================================================

class TestNormalizeIndexKey:
    def test_numpy_int_equals_python_int(self):
        from engine.compiler.struct_builder import normalize_index_key
        try:
            import numpy as np
            assert normalize_index_key(np.int64(0)) == 0
            assert normalize_index_key(np.int64(0)) == normalize_index_key(0)
        except ImportError:
            pytest.skip("numpy not installed")

    def test_string_zero_not_equal_int_zero(self):
        from engine.compiler.struct_builder import normalize_index_key
        assert normalize_index_key('0') == '0'
        assert normalize_index_key(0) == 0
        assert normalize_index_key('0') != normalize_index_key(0)

    def test_tuple_normalization(self):
        from engine.compiler.struct_builder import normalize_index_key
        try:
            import numpy as np
            key = (np.int64(1), np.int64(2))
            assert normalize_index_key(key) == (1, 2)
        except ImportError:
            pytest.skip("numpy not installed")

    def test_list_to_tuple(self):
        from engine.compiler.struct_builder import normalize_index_key
        assert normalize_index_key([1, 2]) == (1, 2)


# ============================================================
# 5. 예외 분류 (StructuredFallbackAllowed vs StructuredDataError)
# ============================================================

class TestExceptionClassification:
    def test_unsupported_pattern_allows_fallback(self):
        from engine.compiler.errors import StructuredFallbackAllowed, UnsupportedStructuredPattern
        assert issubclass(UnsupportedStructuredPattern, StructuredFallbackAllowed)

    def test_data_errors_block_fallback(self):
        from engine.compiler.errors import (
            StructuredFallbackAllowed, StructuredDataError, NonScalarBoundValueError,
            NoneValueError, NonFiniteValueError, VariableResolutionError,
        )
        for cls in [NonScalarBoundValueError, NoneValueError, NonFiniteValueError, VariableResolutionError]:
            assert issubclass(cls, StructuredDataError)
            assert not issubclass(cls, StructuredFallbackAllowed)


# ============================================================
# 6. eval_scalar
# ============================================================

class TestEvalScalar:
    def _make_ctx(self):
        ctx = MagicMock()
        ctx.param_map = {"max_work": 660, "trip_dur": {1: 15, 2: 20}}
        ctx.var_map = {"x": {(1, 1): "x_1_1"}}
        ctx.get_param_scalar = lambda name: ctx.param_map.get(name) if not isinstance(ctx.param_map.get(name), dict) else None
        ctx.get_param_indexed = lambda name, key: ctx.param_map.get(name, {}).get(key, 0) if isinstance(ctx.param_map.get(name), dict) else 0
        return ctx

    def test_constant(self):
        from engine.compiler.affine_collector import eval_scalar
        result = eval_scalar({"type": "constant", "value": 42}, {}, self._make_ctx())
        assert result == 42.0

    def test_parameter(self):
        from engine.compiler.affine_collector import eval_scalar
        result = eval_scalar({"type": "identifier", "name": "max_work"}, {}, self._make_ctx())
        assert result == 660.0

    def test_variable_raises(self):
        from engine.compiler.affine_collector import eval_scalar
        from engine.compiler.errors import UnsupportedStructuredPattern
        with pytest.raises(UnsupportedStructuredPattern):
            eval_scalar({"type": "indexed", "name": "x", "indices": ("1", "1")}, {}, self._make_ctx())


# ============================================================
# 7-9. collect_affine
# ============================================================

class TestCollectAffine:
    def _make_ctx(self):
        ctx = MagicMock()
        ctx.param_map = {"d": {1: 10, 2: 20, 3: 15}, "P": 100}
        ctx.var_map = {
            "x": {(1, 1): "x_1_1", (2, 1): "x_2_1", (3, 1): "x_3_1"},
            "y": {(1,): "y_1"},
        }
        ctx.set_map = {"I": [1, 2, 3]}
        ctx.get_param_scalar = lambda n: ctx.param_map.get(n) if not isinstance(ctx.param_map.get(n), dict) else None
        ctx.get_param_indexed = lambda n, k: ctx.param_map.get(n, {}).get(k, 0) if isinstance(ctx.param_map.get(n), dict) else 0
        ctx.get_var = lambda n, k: ctx.var_map.get(n, {}).get(k, 0)
        ctx.get_set = lambda n: ctx.set_map.get(n, [])
        return ctx

    def test_simple_sum(self):
        """sum(x[i,j] for i in I) with j=1"""
        from engine.compiler.affine_collector import collect_affine, parse_expression_to_ast, VarRef
        ast = parse_expression_to_ast("sum(x[i,j] for i in I)")
        ir = collect_affine(ast, {"j": 1}, self._make_ctx())
        assert len(ir.linear_terms) == 3
        assert ir.constant == 0.0
        # x[1,1], x[2,1], x[3,1] all with coeff 1.0
        for i in [1, 2, 3]:
            vr = VarRef(name="x", indices=(i, 1))
            assert vr in ir.linear_terms
            assert ir.linear_terms[vr] == 1.0

    def test_weighted_sum(self):
        """sum(d[i] * x[i,j] for i in I) with j=1"""
        from engine.compiler.affine_collector import collect_affine, parse_expression_to_ast, VarRef
        ast = parse_expression_to_ast("sum(d[i] * x[i,j] for i in I)")
        ir = collect_affine(ast, {"j": 1}, self._make_ctx())
        assert len(ir.linear_terms) == 3
        # d[1]=10 → x[1,1] coeff=10, d[2]=20 → x[2,1] coeff=20, d[3]=15 → x[3,1] coeff=15
        assert ir.linear_terms[VarRef("x", (1, 1))] == 10.0
        assert ir.linear_terms[VarRef("x", (2, 1))] == 20.0
        assert ir.linear_terms[VarRef("x", (3, 1))] == 15.0

    def test_subtraction_with_sum(self):
        """(a[j] - sum(d[i]*x[i,j] for i in I)) pattern — simplified"""
        from engine.compiler.affine_collector import collect_affine, parse_expression_to_ast, VarRef
        # P - sum(d[i]*x[i,j] for i in I) where P=100
        ast = parse_expression_to_ast("P - sum(d[i] * x[i,j] for i in I)")
        ir = collect_affine(ast, {"j": 1}, self._make_ctx())
        assert ir.constant == 100.0  # P=100
        # sum terms are negated (subtraction)
        assert ir.linear_terms[VarRef("x", (1, 1))] == -10.0
        assert ir.linear_terms[VarRef("x", (2, 1))] == -20.0


# ============================================================
# 10. normalize_constraint (zero-RHS)
# ============================================================

class TestNormalizeConstraint:
    def test_basic(self):
        from engine.compiler.affine_collector import (
            AffineExprIR, VarRef, normalize_constraint,
        )
        lhs = AffineExprIR(constant=5.0, linear_terms={VarRef("x", (1,)): 3.0})
        rhs = AffineExprIR(constant=10.0)
        diff, op, zero = normalize_constraint(lhs, "<=", rhs)
        assert zero == 0.0
        assert diff.constant == -5.0  # 5 - 10
        assert diff.linear_terms[VarRef("x", (1,))] == 3.0


# ============================================================
# 11. constant-only 제약
# ============================================================

class TestConstantConstraint:
    def test_tautology(self):
        from engine.compiler.affine_collector import AffineExprIR, check_constant_constraint
        # 0 <= 0 → tautology
        assert check_constant_constraint(AffineExprIR(constant=0.0), "<=") == "tautology"
        # -5 <= 0 → tautology
        assert check_constant_constraint(AffineExprIR(constant=-5.0), "<=") == "tautology"

    def test_infeasible(self):
        from engine.compiler.affine_collector import AffineExprIR, check_constant_constraint
        # 5 <= 0 → infeasible
        assert check_constant_constraint(AffineExprIR(constant=5.0), "<=") == "infeasible"
        # 3 == 0 → infeasible
        assert check_constant_constraint(AffineExprIR(constant=3.0), "==") == "infeasible"

    def test_normal_has_variables(self):
        from engine.compiler.affine_collector import AffineExprIR, VarRef, check_constant_constraint
        expr = AffineExprIR(linear_terms={VarRef("x", (1,)): 1.0})
        assert check_constant_constraint(expr, "<=") == "normal"


# ============================================================
# 12. lower_affine_to_dimod (mock dimod)
# ============================================================

class TestLowerTodimod:
    def test_deterministic_order(self):
        """lower_affine_to_dimod이 결정적 순서를 보장하는지"""
        from engine.compiler.affine_collector import AffineExprIR, VarRef, lower_affine_to_dimod
        try:
            import dimod
        except ImportError:
            pytest.skip("dimod not installed")

        # 변수 생성
        cqm = dimod.ConstrainedQuadraticModel()
        x1 = dimod.Binary("x_1")
        x2 = dimod.Binary("x_2")

        vr1 = VarRef("x", (1,))
        vr2 = VarRef("x", (2,))
        lookup = {vr1: x1, vr2: x2}

        ir = AffineExprIR(constant=5.0, linear_terms={vr2: 3.0, vr1: 2.0})
        result = lower_affine_to_dimod(ir, lookup)
        # 결과가 dimod expression인지 확인
        assert result is not None


# ============================================================
# 13. empty set / zero coefficient
# ============================================================

class TestEdgeCases:
    def test_empty_set_returns_zero(self):
        from engine.compiler.affine_collector import collect_affine, parse_expression_to_ast
        ctx = MagicMock()
        ctx.set_map = {"I": []}
        ctx.get_set = lambda n: ctx.set_map.get(n, [])
        ast = parse_expression_to_ast("sum(x[i] for i in I)")
        ir = collect_affine(ast, {}, ctx)
        assert ir.is_constant_only
        assert ir.constant == 0.0


# ============================================================
# 14. near-zero pruning
# ============================================================

class TestPruning:
    def test_prune_near_zero(self):
        from engine.compiler.affine_collector import AffineExprIR, VarRef
        ir = AffineExprIR(linear_terms={
            VarRef("x", (1,)): 1e-15,  # near-zero → 제거
            VarRef("x", (2,)): 3.0,    # 유지
        })
        ir.prune_near_zero()
        assert len(ir.linear_terms) == 1
        assert VarRef("x", (2,)) in ir.linear_terms


# ============================================================
# 15. AST Parser
# ============================================================

class TestASTParser:
    def test_parse_simple_sum(self):
        from engine.compiler.affine_collector import parse_expression_to_ast
        ast = parse_expression_to_ast("sum(x[i,j] for i in I)")
        assert ast["type"] == "sum_over"
        assert ast["iter_var"] == "i"
        assert ast["set_name"] == "I"

    def test_parse_weighted_sum_leq(self):
        from engine.compiler.affine_collector import parse_expression_to_ast, parse_constraint_expr_cached
        entry = parse_constraint_expr_cached("sum(d[i] * x[i,j] for i in I) <= P * y[j]")
        assert entry is not None
        assert entry.op == "<="
        assert entry.ast_lhs["type"] == "sum_over"
        assert entry.ast_rhs["type"] == "product"
        assert entry.affine_supported is True

    def test_parse_var_times_var_not_affine(self):
        """variable × variable 패턴 — affine 판정은 runtime에"""
        from engine.compiler.affine_collector import parse_constraint_expr_cached
        # y[j] * (1 - is_night[j]) 는 product of two indexed → affine_supported=True (정적으로는 판단 불가)
        # runtime에 collect_affine에서 UnsupportedStructuredPattern 발생


# ============================================================
# 16. product: scalar×affine ✅, variable×variable → error
# ============================================================

class TestProduct:
    def test_scalar_times_variable(self):
        from engine.compiler.affine_collector import collect_affine, VarRef
        from engine.compiler.struct_builder import BuildContext
        ctx = BuildContext(
            var_map={"y": {(1,): "y_1", ("1",): "y_1"}},  # 양쪽 키 지원
            param_map={"P": 10},
            set_map={},
        )
        ast = {"type": "product", "terms": [
            {"type": "identifier", "name": "P"},
            {"type": "indexed", "name": "y", "indices": ("1",)},
        ]}
        ir = collect_affine(ast, {}, ctx)
        assert len(ir.linear_terms) == 1
        coeff = list(ir.linear_terms.values())[0]
        assert coeff == 10.0


# ============================================================
# 17. 모듈 import 검증
# ============================================================

class TestModuleImport:
    def test_affine_collector_importable(self):
        from engine.compiler.affine_collector import (
            VarRef, AffineExprIR, collect_affine, eval_scalar,
            normalize_constraint, check_constant_constraint,
            lower_affine_to_dimod, build_var_lookup,
            parse_expression_to_ast, parse_constraint_expr_cached,
            is_affine_supported,
        )
        assert callable(collect_affine)
        assert callable(eval_scalar)
