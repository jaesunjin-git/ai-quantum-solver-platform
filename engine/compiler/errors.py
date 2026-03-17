"""
engine/compiler/errors.py
─────────────────────────
컴파일러 예외 계층.

StructuredFallbackAllowed: expression_parser fallback 허용
StructuredDataError: fallback 금지 — 데이터/바인딩 오류
"""


class StructuredBuildError(Exception):
    """구조화 빌드 오류 — 모든 컴파일러 예외의 베이스"""
    pass


# ── fallback 허용 ──
class StructuredFallbackAllowed(StructuredBuildError):
    """expression_parser로 우회 허용되는 패턴 미지원 오류"""
    pass


class UnsupportedStructuredPattern(StructuredFallbackAllowed):
    """구조화 빌더가 지원하지 않는 AST 패턴"""
    pass


class UnsupportedAggregator(StructuredFallbackAllowed):
    """지원하지 않는 집계 함수 (min, max 등)"""
    pass


# ── fallback 금지 — 데이터 오류 ──
class StructuredDataError(StructuredBuildError):
    """데이터/바인딩 오류 — fallback하면 안 됨"""
    pass


class NonScalarBoundValueError(StructuredDataError):
    """scalar여야 할 값이 ndarray/list 등 non-scalar"""
    pass


class NoneValueError(StructuredDataError):
    """None이 들어옴"""
    pass


class NonFiniteValueError(StructuredDataError):
    """NaN, inf, -inf 등 유한하지 않은 값"""
    pass


class VariableResolutionError(StructuredDataError):
    """변수를 찾을 수 없음"""
    pass


class UnresolvedIndexError(StructuredDataError):
    """인덱스를 해석할 수 없음"""
    pass
