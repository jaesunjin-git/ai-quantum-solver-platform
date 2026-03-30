"""
feasibility/base.py — FeasibilityCheck 인터페이스 + Registry + Pipeline
======================================================================
모든 column feasibility check의 기반 구조.

check type별 handler를 registry에 등록하고,
YAML에서 선언된 check 목록을 순차 적용하는 파이프라인.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Check 결과 ──────────────────────────────────────────────

@dataclass
class CheckResult:
    """개별 feasibility check 결과"""
    feasible: bool
    penalty: float = 0.0        # soft check 시 penalty (feasible=True + penalty>0)
    reason: str = ""            # reject/penalty 사유 (로깅용)


# ── Check 인터페이스 ────────────────────────────────────────

class FeasibilityCheck(ABC):
    """
    모든 feasibility check handler의 기반 클래스.

    각 handler는 check_type 이름으로 registry에 등록되며,
    YAML에서 해당 type을 선언하면 pipeline이 자동 호출.

    파라미터 해석 규칙 (_param 접미사):
      - `limit_param: max_idle_time` → params에서 'max_idle_time' 값 조회
      - `limit: 360` → 360을 직접 사용
      - 둘 다 있으면 _param 우선, 직접 값은 fallback
    """

    @abstractmethod
    def check(self, column: Any, config: Dict[str, Any],
              params: Dict[str, Any]) -> CheckResult:
        """
        column의 feasibility를 판정.

        Args:
            column: FeasibleColumn 인스턴스
            config: YAML에서 읽은 이 check의 설정 (type, field, limit_param 등)
            params: 런타임 파라미터 (confirmed_problem + generator config 병합)

        Returns:
            CheckResult(feasible, penalty, reason)
        """
        ...


# ── 파라미터 해석 헬퍼 ──────────────────────────────────────

def resolve_param(config: Dict, key_base: str, params: Dict,
                  default: Any = None) -> Any:
    """_param 접미사 규칙에 따라 값을 해석.

    1. config[key_base + '_param']이 있으면 → params에서 해당 키로 조회
    2. config[key_base]가 있으면 → 직접 값 사용
    3. 둘 다 없으면 → default 반환

    예: resolve_param(config, 'limit', params)
        → config['limit_param']='max_idle_time' → params['max_idle_time']
        → 없으면 config['limit']=360 → 360
    """
    # 1순위: _param 참조
    param_key = config.get(f"{key_base}_param")
    if param_key:
        val = params.get(param_key)
        if val is not None:
            return val

    # 2순위: 직접 값
    direct_val = config.get(key_base)
    if direct_val is not None:
        return direct_val

    return default


# ── Registry ────────────────────────────────────────────────

class FeasibilityCheckRegistry:
    """check type name → handler class 매핑.

    engine은 built-in handler를 제공하고,
    도메인은 custom handler를 등록하여 engine 코드 수정 없이 확장.
    """
    _handlers: Dict[str, type] = {}

    @classmethod
    def register(cls, type_name: str, handler_cls: type):
        """handler 등록. 동일 이름 재등록 시 덮어씀 (도메인 override 허용)."""
        cls._handlers[type_name] = handler_cls

    @classmethod
    def get(cls, type_name: str) -> Optional[type]:
        """등록된 handler 조회. 미등록 시 None."""
        return cls._handlers.get(type_name)

    @classmethod
    def registered_types(cls) -> List[str]:
        """등록된 모든 type 이름 목록."""
        return list(cls._handlers.keys())


# ── Pipeline ────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """파이프라인 전체 실행 결과"""
    feasible: bool
    total_penalty: float = 0.0
    reject_reason: str = ""
    checks_run: int = 0
    checks_passed: int = 0


class FeasibilityPipeline:
    """YAML 선언 기반 feasibility check 파이프라인.

    Usage:
        pipeline = FeasibilityPipeline(checks_config)
        result = pipeline.run(column, params)
        if not result.feasible:
            return None  # reject
    """

    def __init__(self, checks_config: List[Dict[str, Any]]):
        """
        Args:
            checks_config: YAML에서 읽은 check 목록.
                [{type: 'max_value', field: 'idle_minutes', limit_param: 'max_idle_time', action: 'reject'}, ...]
        """
        self._checks: List[tuple] = []  # (handler_instance, config, action)
        self._load_errors: List[str] = []

        for i, cfg in enumerate(checks_config):
            check_type = cfg.get("type")
            if not check_type:
                self._load_errors.append(f"Check #{i}: missing 'type' field")
                continue

            handler_cls = FeasibilityCheckRegistry.get(check_type)
            if handler_cls is None:
                self._load_errors.append(
                    f"Check #{i}: unknown type '{check_type}' "
                    f"(registered: {FeasibilityCheckRegistry.registered_types()})"
                )
                continue

            action = cfg.get("action", "reject")  # 'reject' | 'penalize'
            self._checks.append((handler_cls(), cfg, action))

        if self._load_errors:
            for err in self._load_errors:
                logger.warning(f"FeasibilityPipeline: {err}")

        logger.info(
            f"FeasibilityPipeline: {len(self._checks)} checks loaded, "
            f"{len(self._load_errors)} errors"
        )

    def run(self, column: Any, params: Dict[str, Any]) -> PipelineResult:
        """모든 check를 순차 적용.

        action='reject': check 실패 시 즉시 중단 (feasible=False)
        action='penalize': check 실패 시 penalty 누적 (feasible=True)
        """
        total_penalty = 0.0
        checks_run = 0
        checks_passed = 0

        for handler, cfg, action in self._checks:
            checks_run += 1
            result = handler.check(column, cfg, params)

            if result.feasible:
                checks_passed += 1
                total_penalty += result.penalty
            else:
                if action == "reject":
                    return PipelineResult(
                        feasible=False,
                        total_penalty=total_penalty,
                        reject_reason=result.reason,
                        checks_run=checks_run,
                        checks_passed=checks_passed,
                    )
                else:
                    # penalize: 위반이지만 reject하지 않음
                    checks_passed += 1
                    total_penalty += result.penalty
                    logger.debug(
                        f"Feasibility penalize: {cfg.get('type')} — {result.reason} "
                        f"(penalty={result.penalty:.2f})"
                    )

        return PipelineResult(
            feasible=True,
            total_penalty=total_penalty,
            checks_run=checks_run,
            checks_passed=checks_passed,
        )

    @property
    def check_count(self) -> int:
        return len(self._checks)

    @property
    def load_errors(self) -> List[str]:
        return list(self._load_errors)
