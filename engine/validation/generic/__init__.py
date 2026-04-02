"""
engine.validation.generic — 플랫폼 공통 검증기 패키지.

도메인에 무관하게 모든 최적화 파이프라인에 적용되는 검증기를 모아둡니다.
도메인 고유 검증 규칙은 코드가 아닌 YAML 설정 파일에서 관리합니다.

스테이지별 검증기:
  Stage 1 (업로드)    : FileTypeValidator, FileSizeValidator, EmptyFileValidator, DuplicateFileValidator
  Stage 3 (문제정의)  : ObjectiveValidator, ConstraintValidator, ParameterValidator
  Stage 4 (정규화)    : MappingConfidenceValidator, TransformIntegrityValidator, ColumnMappingValidator
  Stage 5 (컴파일)    : CompileQualityValidator, VariableBoundValidator, ObjectiveExprValidator
  Stage 5 (거점정책)  : DepotPolicyValidator
  Stage 6 (솔버 후)   : StatusValidator, KpiRangeValidator, ConstraintSatValidator, DepotSolutionValidator
"""

from engine.validation.generic.upload import (
    DuplicateFileValidator,
    EmptyFileValidator,
    FileSizeValidator,
    FileTypeValidator,
)
from engine.validation.generic.solution import (
    CompileQualityValidator,
    ConstraintSatisfactionValidator,
    OptimalityGapValidator,
    SolutionStatusValidator,
)
from engine.validation.generic.cross_rules import (
    ParameterCrossRuleValidator,
    ParameterRangeValidator,
)
from engine.validation.generic.presolve import (
    CompileWarningAnalyzer,
    ConstraintApplyRatioValidator,
    ModelDimensionValidator,
)
from engine.validation.generic.infeasible import (
    InfeasibilityDiagnosisValidator,
)
from engine.validation.generic.presolve_prober import (
    PresolveProber,
)
from engine.validation.generic.normalization import (
    MappingConfidenceValidator,
    TransformIntegrityValidator,
    ColumnMappingValidator,
)
from engine.validation.generic.depot import (
    DepotPolicyValidator,
    DepotSolutionValidator,
)

__all__ = [
    # Stage 1: Upload
    "EmptyFileValidator",
    "DuplicateFileValidator",
    "FileTypeValidator",
    "FileSizeValidator",
    # Stage 3: Problem Definition
    "ParameterCrossRuleValidator",
    "ParameterRangeValidator",
    # Stage 5: Compile / Presolve
    "ModelDimensionValidator",
    "ConstraintApplyRatioValidator",
    "CompileWarningAnalyzer",
    # Stage 4: Normalization
    "MappingConfidenceValidator",
    "TransformIntegrityValidator",
    "ColumnMappingValidator",
    # Stage 6: Post-Solve
    "SolutionStatusValidator",
    "OptimalityGapValidator",
    "ConstraintSatisfactionValidator",
    "CompileQualityValidator",
    "InfeasibilityDiagnosisValidator",
    # Stage 5: Presolve Feasibility Probing
    "PresolveProber",
    # Stage 5/6: Depot
    "DepotPolicyValidator",
    "DepotSolutionValidator",
]


def register_all(registry) -> None:
    """Register all platform-generic validators with the given registry."""
    registry.register_many(
        # Stage 1: Upload
        EmptyFileValidator(),
        DuplicateFileValidator(),
        FileTypeValidator(),
        FileSizeValidator(),
        # Stage 3: Problem Definition
        ParameterCrossRuleValidator(),
        ParameterRangeValidator(),
        # Stage 4: Normalization
        MappingConfidenceValidator(),
        TransformIntegrityValidator(),
        ColumnMappingValidator(),
        # Stage 5: Compile / Presolve
        ModelDimensionValidator(),
        ConstraintApplyRatioValidator(),
        CompileWarningAnalyzer(),
        PresolveProber(),
        # Stage 5: Depot Policy
        DepotPolicyValidator(),
        # Stage 6: Post-Solve
        SolutionStatusValidator(),
        DepotSolutionValidator(),
        OptimalityGapValidator(),
        ConstraintSatisfactionValidator(),
        CompileQualityValidator(),
        InfeasibilityDiagnosisValidator(),
    )
