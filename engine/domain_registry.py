"""
domain_registry.py ────────────────────────────────────────
도메인별 adapter(generator_factory, result_converter) 중앙 레지스트리.

GR-1 준수: engine 계층이 domains/를 직접 import하지 않음.
도메인명 → 모듈 경로 매핑으로 lazy import.

설정: configs/domain_registry.yaml (코드 변경 없이 도메인 추가 가능)

Usage:
    from engine.domain_registry import get_domain_adapter
    adapter = get_domain_adapter("railway")  # lazy import + cache
    if adapter:
        pipeline.set_domain_adapter(**adapter)
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


# ── YAML 로딩 (1회) ─────────────────────────────────────────

_REGISTRY_YAML = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "configs", "domain_registry.yaml",
)

_domain_modules: Dict[str, Dict[str, str]] = {}
_domain_aliases: Dict[str, str] = {}
_loaded = False


def _ensure_loaded():
    """configs/domain_registry.yaml에서 도메인 매핑 로딩 (1회)."""
    global _domain_modules, _domain_aliases, _loaded
    if _loaded:
        return
    _loaded = True

    try:
        with open(_REGISTRY_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"domain_registry.yaml load failed: {e}")
        return

    domains = data.get("domains", {})
    for domain_name, spec in domains.items():
        _domain_modules[domain_name] = {
            k: v for k, v in spec.items() if k != "aliases"
        }
        for alias in spec.get("aliases", []):
            _domain_aliases[alias] = domain_name

    logger.info(
        f"DomainRegistry: {len(_domain_modules)} domains, "
        f"{len(_domain_aliases)} aliases from {_REGISTRY_YAML}"
    )


# ── lazy import 캐시 ────────────────────────────────────────

_adapter_cache: Dict[str, Optional[Dict[str, Any]]] = {}


def get_domain_adapter(domain_name: str) -> Optional[Dict[str, Any]]:
    """도메인명으로 adapter(generator_factory, result_converter) 반환.

    첫 호출 시 lazy import, 이후 캐시.
    반환값은 pipeline.set_domain_adapter(**adapter)에 직접 사용 가능.

    Returns:
        {"generator_factory": callable, "result_converter": callable}
        또는 None (미등록 도메인)
    """
    _ensure_loaded()

    # 별칭 해석
    canonical = _domain_aliases.get(domain_name, domain_name)

    if canonical in _adapter_cache:
        return _adapter_cache[canonical]

    spec = _domain_modules.get(canonical)
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
    _ensure_loaded()
    return list(_domain_modules.keys())


def resolve_domain_alias(name: str) -> str:
    """별칭 → canonical 도메인명 해석."""
    _ensure_loaded()
    return _domain_aliases.get(name, name)
