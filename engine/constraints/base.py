"""
constraints/base.py — SideConstraint 인터페이스 + Registry + Pipeline
====================================================================
SP 모델에 동적으로 제약을 추가하는 범용 프레임워크.

각 handler는 column 전체 목록을 받아 SPConstraint를 생성.
YAML에서 선언된 제약 목록을 순차 적용하는 파이프라인.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from engine.feasibility.base import resolve_param

logger = logging.getLogger(__name__)


# ── 결과 ────────────────────────────────────────────────────

@dataclass
class ConstraintResult:
    """SideConstraint handler의 출력"""
    constraint: Any             # SPConstraint 인스턴스
    description: str = ""       # 로깅/진단용


# ── Handler 인터페이스 ──────────────────────────────────────

class SideConstraintHandler(ABC):
    """
    SP Side Constraint handler의 기반 클래스.

    FeasibilityCheck와의 핵심 차이:
      - FeasibilityCheck: column 1개를 판정 (reject/pass)
      - SideConstraintHandler: column 전체를 받아 SPConstraint를 생성

    YAML에서 type을 선언하면 pipeline이 자동 호출.
    """

    @abstractmethod
    def build(self, columns: List[Any], params: Dict[str, Any],
              config: Dict[str, Any]) -> Optional[ConstraintResult]:
        """
        columns 전체를 분석하여 SPConstraint를 생성.

        Args:
            columns: FeasibleColumn 전체 목록 (solver pool)
            params: 런타임 파라미터 (confirmed_problem + config 병합)
            config: YAML에서 읽은 이 constraint의 설정

        Returns:
            ConstraintResult (SPConstraint 포함) 또는 None (해당 없음)
        """
        ...


# ── Registry ────────────────────────────────────────────────

class SideConstraintRegistry:
    """constraint type name → handler class 매핑.

    engine은 built-in handler를 제공하고,
    도메인은 custom handler를 등록하여 engine 코드 수정 없이 확장.
    """
    _handlers: Dict[str, type] = {}

    @classmethod
    def register(cls, type_name: str, handler_cls: type):
        cls._handlers[type_name] = handler_cls

    @classmethod
    def get(cls, type_name: str) -> Optional[type]:
        return cls._handlers.get(type_name)

    @classmethod
    def registered_types(cls) -> List[str]:
        return list(cls._handlers.keys())


# ── Pipeline ────────────────────────────────────────────────

class SideConstraintPipeline:
    """YAML 선언 기반 SP Side Constraint 파이프라인.

    Usage:
        pipeline = SideConstraintPipeline(constraints_config)
        sp_constraints = pipeline.build_all(columns, params)
        # sp_constraints를 SP problem의 extra_constraints에 추가
    """

    def __init__(self, constraints_config: List[Dict[str, Any]]):
        self._handlers: List[tuple] = []  # (handler_instance, config)
        self._load_errors: List[str] = []

        for i, cfg in enumerate(constraints_config):
            constraint_type = cfg.get("type")
            if not constraint_type:
                self._load_errors.append(f"Constraint #{i}: missing 'type' field")
                continue

            handler_cls = SideConstraintRegistry.get(constraint_type)
            if handler_cls is None:
                self._load_errors.append(
                    f"Constraint #{i}: unknown type '{constraint_type}' "
                    f"(registered: {SideConstraintRegistry.registered_types()})"
                )
                continue

            self._handlers.append((handler_cls(), cfg))

        if self._load_errors:
            for err in self._load_errors:
                logger.warning(f"SideConstraintPipeline: {err}")

        logger.info(
            f"SideConstraintPipeline: {len(self._handlers)} constraints loaded, "
            f"{len(self._load_errors)} errors"
        )

    def build_all(self, columns: List[Any],
                  params: Dict[str, Any]) -> List[Any]:
        """모든 handler를 실행하여 SPConstraint 목록 생성.

        Returns:
            List[SPConstraint] — SP problem의 extra_constraints에 추가할 제약 목록
        """
        results = []

        for handler, cfg in self._handlers:
            try:
                result = handler.build(columns, params, cfg)
                if result is not None:
                    results.append(result.constraint)
                    logger.info(
                        f"SideConstraint '{cfg.get('type')}': {result.description}"
                    )
            except Exception as e:
                logger.warning(
                    f"SideConstraint '{cfg.get('type')}' build failed: {e}"
                )

        return results

    @property
    def constraint_count(self) -> int:
        return len(self._handlers)

    @property
    def load_errors(self) -> List[str]:
        return list(self._load_errors)
