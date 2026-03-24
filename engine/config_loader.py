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
    domain별 param_aliases.yaml 또는 generator_config.yaml의 param_field_mapping 섹션."""
    import yaml

    mapping = {}

    # 범용 기본 매핑 (configs/)
    default_path = "configs/param_field_mapping.yaml"
    if os.path.exists(default_path):
        try:
            with open(default_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            mapping.update(data.get("param_field_mapping", data))
        except Exception as e:
            logger.warning(f"param_field_mapping load failed: {default_path}: {e}")

    # 도메인별 override
    if domain:
        domain_path = f"knowledge/domains/{domain}/param_field_mapping.yaml"
        if os.path.exists(domain_path):
            try:
                with open(domain_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                mapping.update(data.get("param_field_mapping", data))
            except Exception as e:
                logger.warning(f"param_field_mapping load failed: {domain_path}: {e}")

    return mapping
