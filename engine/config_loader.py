"""
config_loader.py ──────────────────────────────────────────────
YAML config → dataclass 필드 로딩 유틸리티.

3계층 설정 구조:
  1순위: params (사용자 데이터, DataBinder 경유)
  2순위: YAML config (도메인별/범용 튜닝, 코드 변경 없이 수정 가능)
  3순위: dataclass 기본값 (최후 fallback)

Engine 설정 통합 파일 구조:
  configs/engine_defaults.yaml              — 플랫폼 공통
  knowledge/domains/{name}/engine_config.yaml — 도메인별 override

  각 파일 내부 섹션:
    generator:        Column Generator 튜닝
    feasibility:      Column Feasibility Check 파이프라인
    objective:        Objective Builder 가중치
    side_constraints: SP Side Constraint (Phase 2)
"""

import logging
import os
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ── 통합 Engine Config 로딩 ─────────────────────────────────

def _get_engine_yaml_paths(domain: Optional[str] = None) -> list:
    """Engine config YAML 경로 목록 (탐색 순서대로)."""
    paths = ["configs/engine_defaults.yaml"]
    if domain:
        paths.append(f"knowledge/domains/{domain}/engine_config.yaml")
    return paths


def _load_engine_section(section: str, domain: Optional[str] = None) -> dict:
    """통합 engine YAML에서 특정 섹션을 로딩.
    뒤의 파일이 앞의 파일을 shallow merge.

    Args:
        section: 'generator', 'feasibility', 'objective', 'side_constraints'
        domain: 도메인명 (None이면 플랫폼 공통만)

    Returns:
        해당 섹션의 dict (없으면 빈 dict)
    """
    result = {}
    for path in _get_engine_yaml_paths(domain):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            section_data = data.get(section)
            if section_data is not None:
                if isinstance(section_data, dict):
                    result.update(section_data)
                else:
                    result = section_data  # list 등 non-dict (side_constraints)
        except Exception as e:
            logger.warning(f"Engine config load failed: {path}#{section}: {e}")
    return result


# ── Generator Config 로딩 ───────────────────────────────────

def _apply_dict_to_dataclass(instance: Any, values: dict) -> int:
    """dict의 key-value를 dataclass 필드에 타입 변환하며 적용."""
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
    return applied


def load_yaml_into_dataclass(instance: Any, *yaml_paths: str) -> None:
    """여러 YAML 파일을 순서대로 로딩하여 dataclass 필드에 적용.
    뒤의 파일이 앞의 파일을 override.
    파일이 없으면 무시 (에러 없음).

    통합 engine_config.yaml과 개별 파일 모두 지원:
      - 통합: generator/feasibility/objective 섹션이 있으면 generator 섹션만 적용
      - 개별: flat 구조면 전체 적용 (하위 호환)
    """
    for path in yaml_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                values = yaml.safe_load(f) or {}

            # 통합 파일: generator 섹션이 있으면 해당 섹션만 적용
            if "generator" in values and isinstance(values["generator"], dict):
                applied = _apply_dict_to_dataclass(instance, values["generator"])
            else:
                # 개별 파일 (하위 호환): flat 구조
                applied = _apply_dict_to_dataclass(instance, values)

            logger.info(f"Config loaded: {path} ({applied} values applied)")
        except Exception as e:
            logger.warning(f"Config load failed: {path}: {e}")


def get_generator_yaml_paths(domain: Optional[str] = None) -> list:
    """Generator config YAML 파일 경로 목록.
    통합 파일과 개별 파일 모두 탐색 (통합 우선)."""
    paths = []

    # 통합 파일
    for p in _get_engine_yaml_paths(domain):
        if os.path.exists(p):
            paths.append(p)

    # 개별 파일 fallback (하위 호환)
    if not paths:
        paths.append("configs/generator_defaults.yaml")
        if domain:
            paths.append(f"knowledge/domains/{domain}/generator_config.yaml")

    return paths


# ── Objective Config 로딩 ───────────────────────────────────

def load_objective_config(domain: Optional[str] = None) -> dict:
    """Objective 설정을 통합 YAML에서 로딩.
    반환: {duty_weight, short_penalty_weight, ...}"""
    result = _load_engine_section("objective", domain)
    if result:
        logger.info(f"Objective config loaded from engine_config (domain={domain})")
    return result


def get_objective_yaml_paths(domain: Optional[str] = None) -> list:
    """Objective config YAML 경로 (하위 호환용).
    통합 파일이 있으면 통합, 없으면 개별 파일."""
    paths = _get_engine_yaml_paths(domain)
    # 개별 파일 fallback
    if not any(os.path.exists(p) for p in paths):
        paths = ["configs/objective_defaults.yaml"]
        if domain:
            paths.append(f"knowledge/domains/{domain}/objective_config.yaml")
    return paths


# ── Feasibility Config 로딩 ─────────────────────────────────

def load_feasibility_checks(domain: Optional[str] = None) -> list:
    """Feasibility check 목록을 통합 YAML에서 로딩.
    도메인 YAML의 feasibility.checks가 있으면 defaults를 완전히 대체.
    반환: [{type, field, limit_param, action, ...}, ...]"""

    # 도메인 통합 파일에서 먼저 시도
    if domain:
        domain_path = f"knowledge/domains/{domain}/engine_config.yaml"
        if os.path.exists(domain_path):
            try:
                with open(domain_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                feas = data.get("feasibility", {})
                if isinstance(feas, dict) and "checks" in feas:
                    checks = feas["checks"]
                    logger.info(f"Feasibility checks loaded: {domain_path} ({len(checks)} checks)")
                    return checks
            except Exception as e:
                logger.warning(f"Feasibility config load failed: {domain_path}: {e}")

    # 플랫폼 공통 통합 파일
    defaults_path = "configs/engine_defaults.yaml"
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            feas = data.get("feasibility", {})
            if isinstance(feas, dict) and "checks" in feas:
                checks = feas["checks"]
                logger.info(f"Feasibility checks loaded: {defaults_path} ({len(checks)} checks)")
                return checks
        except Exception as e:
            logger.warning(f"Feasibility config load failed: {defaults_path}: {e}")

    return []


# ── Side Constraints 로딩 (Phase 2) ─────────────────────────

def load_side_constraints(domain: Optional[str] = None) -> list:
    """SP Side Constraint 목록을 통합 YAML에서 로딩.
    반환: [{type, column_attribute, operator, ...}, ...]"""
    result = _load_engine_section("side_constraints", domain)
    if isinstance(result, list):
        return result
    return []


# ── Param Field Mapping (별도 파일 유지) ────────────────────

def load_param_field_mapping(domain: Optional[str] = None) -> dict:
    """params 키 → config 필드 매핑을 YAML에서 로딩.
    반환 형식: {config_field: {priority: [param_key1, param_key2, ...]}}
    또는 legacy 형식: {param_key: config_field}"""
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
    """YAML 매핑을 기반으로 params → config 필드에 값 적용."""
    mapping = load_param_field_mapping(domain)
    applied = 0

    for field_or_key, spec in mapping.items():
        if isinstance(spec, dict) and "priority" in spec:
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
                    break
        elif isinstance(spec, str):
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
