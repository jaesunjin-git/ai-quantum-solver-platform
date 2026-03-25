"""
domain_registry.py ────────────────────────────────────────
도메인별 adapter(generator_factory, result_converter) 중앙 레지스트리.

GR-1 준수: engine 계층이 domains/를 직접 import하지 않음.
도메인명 → 모듈 경로 매핑으로 lazy import.

Usage:
    from engine.domain_registry import get_domain_adapter
    adapter = get_domain_adapter("railway")  # lazy import + cache
    if adapter:
        pipeline.set_domain_adapter(**adapter)
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 도메인명 → 모듈 경로 + factory/converter 함수명 매핑
# 새 도메인 추가 = 이 dict에 1줄 추가 (코드 변경 최소화)
_DOMAIN_MODULES: Dict[str, Dict[str, str]] = {
    "railway": {
        "module": "domains.crew.duty_generator",
        "generator_class": "CrewDutyGenerator",
        "config_class": "CrewDutyConfig",
        "converter_module": "domains.crew.result_converter",
        "converter_func": "convert_crew_result",
    },
    # 향후 도메인 추가 예시:
    # "logistics": {
    #     "module": "domains.logistics.generator",
    #     "generator_class": "LogisticsGenerator",
    #     "config_class": "LogisticsConfig",
    #     "converter_module": "domains.logistics.result_converter",
    #     "converter_func": "convert_logistics_result",
    # },
}

# 별칭 매핑 (동일 도메인의 다른 이름)
_DOMAIN_ALIASES: Dict[str, str] = {
    "crew": "railway",
    "rail": "railway",
}

# lazy import 캐시
_adapter_cache: Dict[str, Optional[Dict[str, Any]]] = {}


def get_domain_adapter(domain_name: str) -> Optional[Dict[str, Any]]:
    """도메인명으로 adapter(generator_factory, result_converter) 반환.

    첫 호출 시 lazy import, 이후 캐시.
    반환값은 pipeline.set_domain_adapter(**adapter)에 직접 사용 가능.

    Returns:
        {"generator_factory": callable, "result_converter": callable}
        또는 None (미등록 도메인)
    """
    # 별칭 해석
    canonical = _DOMAIN_ALIASES.get(domain_name, domain_name)

    if canonical in _adapter_cache:
        return _adapter_cache[canonical]

    spec = _DOMAIN_MODULES.get(canonical)
    if not spec:
        logger.warning(f"Domain '{domain_name}' not registered in domain_registry")
        _adapter_cache[canonical] = None
        return None

    try:
        # generator 모듈 lazy import
        gen_mod = importlib.import_module(spec["module"])
        generator_class = getattr(gen_mod, spec["generator_class"])
        config_class = getattr(gen_mod, spec["config_class"])

        # converter 모듈 lazy import
        conv_mod = importlib.import_module(spec["converter_module"])
        converter_func = getattr(conv_mod, spec["converter_func"])

        adapter = {
            "generator_factory": lambda tasks, params, _gc=generator_class, _cc=config_class: _gc(
                tasks, _cc.from_params(params)
            ),
            "result_converter": converter_func,
        }
        _adapter_cache[canonical] = adapter
        logger.info(f"Domain adapter loaded: '{canonical}' ({spec['module']})")
        return adapter

    except (ImportError, AttributeError) as e:
        logger.warning(f"Domain adapter '{canonical}' load failed: {e}")
        _adapter_cache[canonical] = None
        return None


def list_registered_domains() -> list:
    """등록된 도메인 목록 반환."""
    return list(_DOMAIN_MODULES.keys())
