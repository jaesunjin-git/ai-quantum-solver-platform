# engine/compiler/base.py
# ============================================================
# Model Compiler Base: IR JSON -> Solver-specific model
# ============================================================
#
# DataBinder는 engine/compiler/data_binder.py로 분리됨.
# 기존 import 호환을 위해 여기서 re-export.
# ============================================================

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Re-export DataBinder for backward compatibility
from engine.compiler.data_binder import DataBinder  # noqa: F401

logger = logging.getLogger(__name__)


# ============================================================
# CompileResult: 컴파일 결과 컨테이너
# ============================================================
@dataclass
class CompileResult:
    """컴파일러 출력"""
    success: bool
    solver_model: Any = None          # 솔버별 모델 객체
    solver_type: str = ""             # "ortools_cp", "ortools_lp", "cqm", "bqm"
    variable_count: int = 0
    constraint_count: int = 0
    variable_map: Dict[str, Any] = field(default_factory=dict)  # IR변수ID -> 솔버변수
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)




# ============================================================
# BaseCompiler: 추상 컴파일러
# ============================================================
class BaseCompiler(ABC):
    """모든 솔버 컴파일러의 기본 클래스"""

    @abstractmethod
    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """
        수학 모델 IR + 바인딩된 데이터 -> 솔버별 모델 객체

        Args:
            math_model: IR JSON (sets, variables, objective, constraints, ...)
            bound_data: DataBinder.bind_all() 결과
            **kwargs: 솔버별 추가 옵션

        Returns:
            CompileResult
        """
        ...

    def _get_variable_type(self, var_def: Dict) -> str:
        """IR 변수 타입을 정규화"""
        vtype = var_def.get("type", "binary").lower().strip()
        aliases = {
            "numeric": "continuous",
            "float": "continuous",
            "real": "continuous",
            "bool": "binary",
            "boolean": "binary",
            "int": "integer",
        }
        return aliases.get(vtype, vtype)

    def _compute_set_product(self, indices: List[str], bound_data: Dict) -> List[tuple]:
        """변수의 indices에 해당하는 집합들의 데카르트 곱을 계산"""
        from itertools import product

        sets_values = []
        for idx in indices:
            values = bound_data.get("sets", {}).get(idx, [])
            if not values:
                logger.warning(f"Empty set for index: {idx}")
                return []
            sets_values.append(values)

        return list(product(*sets_values))


class BaseSPCompiler(BaseCompiler):
    """Set Partitioning 컴파일러 공통 기반.

    SP problem 구축/검증/에러 처리를 공통화.
    서브클래스는 _compile_backend()만 구현.
    """

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """Template method: SP init → validate → backend compile"""
        from engine.compiler.sp_problem import SetPartitioningProblem, build_sp_problem
        from engine.column_generator import FeasibleColumn

        # SP problem 구축 (또는 직접 제공)
        sp_problem = kwargs.pop("sp_problem", None)
        if sp_problem is None:
            columns: list = kwargs.pop("duties", [])
            if not columns:
                return CompileResult(
                    success=False,
                    error="No columns provided. Run ColumnGenerator first.",
                )
            params = bound_data.get("parameters", {})
            sp_problem = build_sp_problem(columns, params)

        # 유효성 검증 (infeasibility_reasons 포함)
        valid, errors, warnings = sp_problem.validate()
        for w in warnings:
            logger.warning(f"{self.__class__.__name__}: {w}")
        if not valid:
            return CompileResult(
                success=False,
                error=f"SP problem invalid: {'; '.join(errors)}",
                metadata={"sp_diagnostics": sp_problem.diagnostics},
            )

        # 서브클래스 backend 컴파일
        try:
            return self._compile_backend(sp_problem, math_model, bound_data, **kwargs)
        except ImportError as e:
            logger.error(f"SDK not available: {e}")
            return CompileResult(success=False, error=f"SDK not available: {e}")
        except Exception as e:
            logger.error(f"{self.__class__.__name__} compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    @abstractmethod
    def _compile_backend(self, sp_problem, math_model: Dict,
                          bound_data: Dict, **kwargs) -> CompileResult:
        """서브클래스 구현: SP problem → solver-specific 모델 변환"""
        ...
