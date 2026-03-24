"""
config_loader.py ──────────────────────────────────────────────
YAML config → dataclass 필드 로딩 유틸리티.

3계층 설정 구조:
  1순위: params (사용자 데이터, DataBinder 경유)
  2순위: YAML config (도메인별/범용 튜닝, 코드 변경 없이 수정 가능)
  3순위: dataclass 기본값 (최후 fallback)
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def load_yaml_into_dataclass(instance: Any, *yaml_paths: str) -> None:
    """여러 YAML 파일을 순서대로 로딩하여 dataclass 필드에 적용.
    뒤의 파일이 앞의 파일을 override.
    파일이 없으면 무시 (에러 없음)."""
    import yaml

    for path in yaml_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                values = yaml.safe_load(f) or {}
            applied = 0
            for key, val in values.items():
                if hasattr(instance, key) and val is not None:
                    current = getattr(instance, key)
                    try:
                        if isinstance(current, bool):
                            setattr(instance, key, bool(val))
                        elif isinstance(current, int):
                            setattr(instance, key, int(val))
                        elif isinstance(current, float):
                            setattr(instance, key, float(val))
                        elif isinstance(current, str):
                            setattr(instance, key, str(val))
                        else:
                            setattr(instance, key, val)
                        applied += 1
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Config type error: {key}={val}: {e}")
            logger.info(f"Config loaded: {path} ({applied} values applied)")
        except Exception as e:
            logger.warning(f"Config load failed: {path}: {e}")


def get_generator_yaml_paths(domain: Optional[str] = None) -> list:
    """Generator config YAML 파일 경로 목록 (탐색 순서대로).
    뒤의 파일이 앞의 파일을 override."""
    paths = ["configs/generator_defaults.yaml"]
    if domain:
        paths.append(f"knowledge/domains/{domain}/generator_config.yaml")
    return paths


def load_param_field_mapping(domain: Optional[str] = None) -> dict:
    """params 키 → config 필드 매핑을 YAML에서 로딩.
    반환 형식: {config_field: {priority: [param_key1, param_key2, ...]}}
    또는 legacy 형식: {param_key: config_field}"""
    import yaml

    mapping = {}

    paths = ["configs/param_field_mapping.yaml"]
    if domain:
        paths.append(f"knowledge/domains/{domain}/param_field_mapping.yaml")

    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            raw = data.get("param_field_mapping", data)
            mapping.update(raw)
        except Exception as e:
            logger.warning(f"param_field_mapping load failed: {path}: {e}")

    return mapping


def apply_param_mapping(cfg: Any, params: dict, domain: Optional[str] = None) -> int:
    """YAML 매핑을 기반으로 params → config 필드에 값 적용.

    priority 형식: {config_field: {priority: [key1, key2]}} — key1 우선
    legacy 형식: {param_key: config_field} — 단일 매핑

    Returns: 적용된 필드 수
    """
    mapping = load_param_field_mapping(domain)
    applied = 0

    for field_or_key, spec in mapping.items():
        if isinstance(spec, dict) and "priority" in spec:
            # priority 형식: field_or_key = config 필드명, spec.priority = [param_key, ...]
            config_field = field_or_key
            for param_key in spec["priority"]:
                val = params.get(param_key)
                if val is not None:
                    if not hasattr(cfg, config_field):
                        break
                    try:
                        current = getattr(cfg, config_field)
                        if isinstance(current, int):
                            setattr(cfg, config_field, int(val))
                        elif isinstance(current, float):
                            setattr(cfg, config_field, float(val))
                        elif isinstance(current, bool):
                            setattr(cfg, config_field, bool(val))
                        else:
                            setattr(cfg, config_field, val)
                        applied += 1
                    except (ValueError, TypeError):
                        pass
                    break  # 첫 번째 유효한 값만 사용
        elif isinstance(spec, str):
            # legacy 형식: field_or_key = param_key, spec = config 필드명
            param_key = field_or_key
            config_field = spec
            val = params.get(param_key)
            if val is not None and hasattr(cfg, config_field):
                try:
                    current = getattr(cfg, config_field)
                    if isinstance(current, int):
                        setattr(cfg, config_field, int(val))
                    elif isinstance(current, float):
                        setattr(cfg, config_field, float(val))
                    applied += 1
                except (ValueError, TypeError):
                    pass

    return applied
