"""
engine/result_interpreter_base.py
솔버 결과 해석기 베이스 클래스 — YAML 기반, 도메인 확장 가능.

솔버가 반환한 원시 솔루션(변수 값)을 사람이 읽을 수 있는 해석 결과로 변환합니다.
도메인별 result_mapping.yaml을 로드하여 목적함수 분류, 제약 라벨, KPI 정의 등을
코드 수정 없이 외부 설정으로 관리할 수 있게 합니다.

구조:
    GenericResultInterpreter (베이스)
      └─ knowledge/domains/{domain}/result_mapping.yaml 로드
      └─ 기본 interpret / save_artifacts 로직 제공
      └─ 도메인별 서브클래스가 훅을 오버라이드

    get_interpreter(domain) → 해당 도메인의 인터프리터 인스턴스 반환

새 도메인 추가 시:
  1. knowledge/domains/{domain}/result_mapping.yaml 작성
  2. (선택) engine/interpreters/{domain}.py에 서브클래스 작성
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(__file__).resolve().parent.parent / "knowledge" / "domains"


def _load_result_mapping(domain: str) -> dict:
    """Load result_mapping.yaml for a domain. Returns empty dict if missing."""
    yaml_path = KNOWLEDGE_BASE / domain / "result_mapping.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_index_key(key: str) -> tuple:
    """Extract numeric indices from a solution key like 'x[3,7]'."""
    nums = re.findall(r"\d+", key)
    return tuple(int(n) for n in nums) if nums else ()


def _min_to_hhmm(minutes: float) -> str:
    """Convert minutes to HH:MM format."""
    if minutes is None:
        return "--:--"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


class GenericResultInterpreter:
    """
    Base result interpreter that reads result_mapping.yaml.

    Subclasses can override:
      - parse_solution()     — extract entities from raw solution
      - compute_kpi()        — domain-specific KPI aggregation
      - check_constraint()   — per-constraint post-solve validation
      - build_artifacts()    — custom artifact generation
    """

    def __init__(self, domain: str = "generic"):
        self.domain = domain
        self.mapping = _load_result_mapping(domain)

    # ── Objective classification ──

    def classify_objective(self, expression: str) -> Tuple[str, str]:
        """
        Classify objective expression → (obj_type, label_ko).
        Reads patterns from result_mapping.yaml.
        """
        obj_types = self.mapping.get("objective_types", {})
        expr_lower = (expression or "").lower()

        for obj_type, config in obj_types.items():
            for pat in config.get("patterns", []):
                if pat.lower() in expr_lower:
                    return obj_type, config.get("label_ko", obj_type)

        default_label = self.mapping.get("default_objective_label_ko", "최적화 결과")
        return "generic", default_label

    # ── Constraint labels ──

    def get_hard_label(self, constraint_name: str) -> str:
        labels = self.mapping.get("constraint_labels", {}).get("hard", {})
        return labels.get(constraint_name, constraint_name)

    def get_soft_label(self, constraint_name: str) -> str:
        labels = self.mapping.get("constraint_labels", {}).get("soft", {})
        return labels.get(constraint_name, constraint_name)

    # ── Parameter defaults ──

    def get_param(self, params: dict, key: str) -> float:
        """Get parameter with fallback to mapping defaults."""
        if key in params:
            return float(params[key])
        defaults = self.mapping.get("parameter_defaults", {})
        return float(defaults.get(key, 0))

    # ── Data loading ──

    def load_entity_data(self, project_dir: str):
        """Load the primary entity data (e.g., trips.csv). Returns pandas DataFrame."""
        import pandas as pd

        entity = self.mapping.get("entity", {})
        data_file = entity.get("data_file", "normalized/trips.csv")
        fallback = entity.get("data_fallback", "")

        path = os.path.join(project_dir, data_file)
        if not os.path.exists(path) and fallback:
            path = os.path.join(project_dir, fallback)

        return pd.read_csv(path)

    def load_parameters(self, project_dir: str) -> dict:
        """Load parameters.csv → dict. semantic_id 기반 키 + param_name 키 병행."""
        import pandas as pd

        params_path = os.path.join(project_dir, "normalized", "parameters.csv")
        if os.path.exists(params_path):
            pf = pd.read_csv(params_path)
            params = {}
            has_semantic = "semantic_id" in pf.columns
            for _, row in pf.iterrows():
                try:
                    val = float(row.iloc[1])
                except (ValueError, TypeError):
                    val = row.iloc[1]
                # param_name 키 (하위 호환)
                params[row.iloc[0]] = val
                # semantic_id 키 (YAML verify 룰과 매칭)
                if has_semantic and pd.notna(row.get("semantic_id")):
                    params[row["semantic_id"]] = val
            return params
        return dict(self.mapping.get("parameter_defaults", {}))

    # ── Solution variable extraction ──

    def get_var_key(self, role: str) -> str:
        """Get the solution variable key for a role (activation, assignment, etc.)."""
        var_config = self.mapping.get("solution_variables", {}).get(role, {})
        return var_config.get("key", role[0])  # fallback: first letter

    # ── Interpretation hooks (override in subclasses) ──

    def interpret(
        self,
        solution: Dict[str, Any],
        math_model: Dict[str, Any],
        project_dir: str,
        solver_id: str = "",
        solver_name: str = "",
        status: str = "",
        objective_value: float = None,
        params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Main interpretation entry point.
        Default implementation returns a minimal generic result.
        Domain subclasses override for rich interpretation.
        """
        obj_expr = math_model.get("objective", {}).get("expression", "")
        obj_type, obj_label = self.classify_objective(obj_expr)

        return {
            "objective_type": obj_type,
            "objective_label": obj_label,
            "objective_value": objective_value,
            "solver_id": solver_id,
            "solver_name": solver_name,
            "status": status,
            "kpi": {},
            "duties": [],
            "constraint_status": [],
            "soft_constraint_status": [],
            "warnings": [],
        }

    def save_artifacts(
        self,
        project_dir: str,
        solution: Dict[str, Any],
        interpreted: Dict[str, Any],
        solver_id: str,
    ) -> Dict[str, str]:
        """Save result artifacts. Default: solution + interpretation JSON."""
        results_dir = os.path.join(project_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        saved = {}

        sol_path = os.path.join(results_dir, f"solution_{solver_id}.json")
        with open(sol_path, "w", encoding="utf-8") as f:
            json.dump(solution, f, ensure_ascii=False, indent=2, default=str)
        saved["solution"] = sol_path

        interp_path = os.path.join(results_dir, f"interpretation_{solver_id}.json")
        with open(interp_path, "w", encoding="utf-8") as f:
            json.dump(interpreted, f, ensure_ascii=False, indent=2, default=str)
        saved["interpretation"] = interp_path

        return saved


# ── Interpreter registry ──

_INTERPRETERS: Dict[str, type] = {}


def register_interpreter(domain: str, cls: type) -> None:
    """Register a domain-specific interpreter class."""
    _INTERPRETERS[domain] = cls


def get_interpreter(domain: str) -> GenericResultInterpreter:
    """
    Get the result interpreter for a domain.
    Falls back to GenericResultInterpreter if no domain-specific one exists.
    """
    cls = _INTERPRETERS.get(domain)
    if cls:
        return cls(domain)

    # Auto-discover: if result_mapping.yaml exists, use GenericResultInterpreter
    if (KNOWLEDGE_BASE / domain / "result_mapping.yaml").exists():
        return GenericResultInterpreter(domain)

    return GenericResultInterpreter("generic")
