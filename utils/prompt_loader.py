import os
import yaml
import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(BASE_DIR, "prompts")


@lru_cache()
def load_prompt(domain: str, filename: str) -> str:
    file_path = os.path.join(PROMPT_DIR, domain, f"{filename}.md")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Prompt file not found: {file_path}")
        return "You are a helpful AI assistant."


@lru_cache()
def load_yaml_prompt(domain: str, filename: str) -> Dict[str, Any]:
    file_path = os.path.join(PROMPT_DIR, domain, f"{filename}.yaml")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"YAML prompt not found: {file_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"YAML parse error in {file_path}: {e}")
        return {}


@lru_cache()
def load_schema(filename: str) -> Dict[str, Any]:
    file_path = os.path.join(PROMPT_DIR, "schemas", f"{filename}.yaml")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"Schema file not found: {file_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Schema parse error in {file_path}: {e}")
        return {}


def build_prompt_from_yaml(
    domain: str,
    filename: str,
    variables: Optional[Dict[str, str]] = None
) -> str:
    config = load_yaml_prompt(domain, filename)
    if not config:
        return ""

    template = config.get("template", "")

    if not template:
        parts = []
        if config.get("system"):
            parts.append(config["system"])
        if config.get("rules"):
            rules_text = "\n".join(
                f"  {i+1}. {r}" for i, r in enumerate(config["rules"])
            )
            parts.append(f"규칙:\n{rules_text}")
        if config.get("schema"):
            parts.append(f"출력 스키마:\n{config['schema']}")
        template = "\n\n".join(parts)

    if variables:
        for key, value in variables.items():
            template = template.replace("{" + key + "}", str(value))

    return template


def get_constraint_schema_text() -> str:
    schema = load_schema("constraint_schema")
    if not schema:
        return ""

    lines = []
    lines.append("제약조건 필드:")
    for name, info in schema.get("constraint_fields", {}).items():
        desc = info.get("description", "")
        req = "필수" if info.get("required") else "선택"
        lines.append(f"  - {name} ({req}): {desc}")

    lines.append("")
    lines.append("LHS/RHS 노드 타입:")
    for name, info in schema.get("node_types", {}).items():
        desc = info.get("description", "")
        example = json.dumps(info.get("example", {}), ensure_ascii=False)
        lines.append(f"  - {name}: {desc}")
        lines.append(f"    예시: {example}")

    lines.append("")
    lines.append("제약 예시:")
    for ex in schema.get("examples", []):
        desc = ex.get("description", "")
        ex_json = json.dumps(ex.get("json", {}), ensure_ascii=False, indent=4)
        lines.append(f"  {desc}:")
        lines.append(f"  {ex_json}")

    return "\n".join(lines)
