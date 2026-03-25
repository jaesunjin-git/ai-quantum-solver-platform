"""
Parameter Catalog — Deterministic Resolver.

Loads parameter_catalog.yaml from domain knowledge packs.
Provides exact match + explicit alias resolution (no fuzzy/similarity).
Validates parameter values against valid_range.
Enforces not_alias_of forbidden mappings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(__file__).resolve().parents[2] / "knowledge" / "domains"


@dataclass
class CatalogEntry:
    id: str
    family: str = ""
    semantic_role: str = ""
    type: str = "scalar"
    unit: str = ""
    valid_range: Optional[list] = None
    aliases: list = field(default_factory=list)
    default_alias: str = ""
    not_alias_of: list = field(default_factory=list)
    related_constraints: list = field(default_factory=list)
    description: str = ""
    semantic_guard: Optional[dict] = None


class ParameterCatalog:
    """Domain parameter catalog for deterministic resolution."""

    def __init__(self, domain: str):
        self._domain = domain
        self._entries: dict[str, CatalogEntry] = {}
        self._alias_map: dict[str, str] = {}  # alias → canonical ID
        self._not_alias_pairs: set[tuple[str, str]] = set()
        self._load(domain)

    def _load(self, domain: str):
        if not domain:
            return
        path = KNOWLEDGE_BASE / domain / "parameter_catalog.yaml"
        if not path.exists():
            logger.debug(f"No parameter_catalog.yaml for domain '{domain}'")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load parameter_catalog.yaml: {e}")
            return

        for pid, pdef in data.get("parameters", {}).items():
            entry = CatalogEntry(
                id=pid,
                family=pdef.get("family", ""),
                semantic_role=pdef.get("semantic_role", ""),
                type=pdef.get("type", "scalar"),
                unit=pdef.get("unit", ""),
                valid_range=pdef.get("valid_range"),
                aliases=pdef.get("aliases", []),
                default_alias=pdef.get("default_alias", ""),
                not_alias_of=pdef.get("not_alias_of", []),
                related_constraints=pdef.get("related_constraints", []),
                description=pdef.get("description", ""),
                semantic_guard=pdef.get("semantic_guard"),
            )
            self._entries[pid] = entry

            # Build alias → canonical map
            for alias in entry.aliases:
                self._alias_map[alias] = pid

            # Build forbidden pairs (bidirectional, 자기 참조 제외)
            for forbidden in entry.not_alias_of:
                if forbidden != pid:
                    self._not_alias_pairs.add((pid, forbidden))
                    self._not_alias_pairs.add((forbidden, pid))

        logger.info(
            f"ParameterCatalog loaded: {len(self._entries)} params, "
            f"{len(self._alias_map)} aliases, {len(self._not_alias_pairs)//2} forbidden pairs"
        )

    def has_catalog(self) -> bool:
        return len(self._entries) > 0

    def resolve(self, name: str) -> Optional[CatalogEntry]:
        """Deterministic resolution: exact match → alias match → None.

        No fuzzy/similarity matching.
        """
        # Exact match
        if name in self._entries:
            return self._entries[name]
        # Alias match
        canonical = self._alias_map.get(name)
        if canonical:
            return self._entries.get(canonical)
        return None

    def get_default_alias(self, name: str) -> Optional[str]:
        """Get the default alias for a parameter (replaces prefix matching).

        Example: preparation_minutes → preparation_minutes_departure
        """
        entry = self._entries.get(name)
        if entry and entry.default_alias:
            return entry.default_alias
        return None

    def is_forbidden_alias(self, name_a: str, name_b: str) -> bool:
        """Check if two parameters are forbidden from being aliases (bidirectional)."""
        return (name_a, name_b) in self._not_alias_pairs

    def validate_value(self, name: str, value: Any) -> Optional[str]:
        """Validate a parameter value against catalog valid_range.

        Returns error message if invalid, None if OK.
        """
        entry = self.resolve(name)
        if not entry or entry.valid_range is None:
            return None

        if entry.type == "boolean":
            return None  # booleans don't have numeric range

        try:
            v = float(value)
        except (ValueError, TypeError):
            return None

        vr = entry.valid_range
        if len(vr) >= 2:
            try:
                lo, hi = float(vr[0]), float(vr[1])
            except (ValueError, TypeError):
                return None  # valid_range 원소가 숫자가 아닌 경우 검증 skip
            if v < lo or v > hi:
                return (
                    f"Parameter '{name}' = {v}: valid_range [{lo}, {hi}] 벗어남 "
                    f"(family={entry.family}, role={entry.semantic_role})"
                )
        return None

    def get_entry(self, name: str) -> Optional[CatalogEntry]:
        """Direct entry lookup (no alias resolution)."""
        return self._entries.get(name)

    def validate_semantic(
        self, name: str, value: Any,
        data_facts: dict = None, current_params: dict = None,
    ) -> Optional[str]:
        """semantic_guard 기반 의미적 검증.

        valid_range만으로 잡을 수 없는 혼동 오류를 감지한다.
        예: total_duties에 trip_count 값이 할당되는 경우.

        Returns:
            error message if blocked, None if OK.
        """
        entry = self.resolve(name)
        if not entry or not entry.semantic_guard:
            return None

        guard = entry.semantic_guard
        data_facts = data_facts or {}
        current_params = current_params or {}

        try:
            v = float(value)
        except (ValueError, TypeError):
            return None

        # confusion guard: 다른 개념의 값과 혼동 감지
        # 현재: guard_ratio (상한 방향). 향후: min_ratio (하한 방향) 확장 가능
        confusion_label = guard.get("confusion_label")
        guard_ratio = guard.get("guard_ratio")
        if confusion_label and guard_ratio is not None:
            ref_value = data_facts.get(confusion_label, 0)
            if ref_value and ref_value > 0 and v >= float(ref_value) * float(guard_ratio):
                return (
                    f"'{name}' = {v}: {confusion_label}({ref_value})과 "
                    f"혼동 의심 (guard_ratio={guard_ratio})"
                )

        # upper_ref guard: 참조 파라미터 이하 검증
        upper_ref = guard.get("upper_ref")
        if upper_ref:
            ref_val = current_params.get(upper_ref, {})
            if isinstance(ref_val, dict):
                ref_val = ref_val.get("value")
            if ref_val is not None:
                try:
                    if v > float(ref_val):
                        return (
                            f"'{name}' = {v}: "
                            f"상한 참조 '{upper_ref}'({ref_val}) 초과"
                        )
                except (ValueError, TypeError):
                    pass

        # lower_ref guard: 참조 파라미터 이상 검증
        lower_ref = guard.get("lower_ref")
        if lower_ref:
            ref_val = current_params.get(lower_ref, {})
            if isinstance(ref_val, dict):
                ref_val = ref_val.get("value")
            if ref_val is not None:
                try:
                    if v < float(ref_val):
                        return (
                            f"'{name}' = {v}: "
                            f"하한 참조 '{lower_ref}'({ref_val}) 미만"
                        )
                except (ValueError, TypeError):
                    pass

        return None

    def build_prompt_hints(
        self, data_facts: dict = None, current_params: dict = None,
    ) -> str:
        """semantic_guard가 있는 파라미터들의 LLM 프롬프트 힌트 생성.

        도메인 무관 — catalog에 선언된 규칙만 사용.
        """
        data_facts = data_facts or {}
        current_params = current_params or {}
        hints = []

        # 템플릿 변수 준비: data_facts + current_params의 value (스칼라 정규화)
        fmt_vars = {}
        for k, v in data_facts.items():
            if isinstance(v, dict):
                fmt_vars[k] = v.get("value", v.get("count", "?"))
            else:
                fmt_vars[k] = v
        for pid, pval in current_params.items():
            if isinstance(pval, dict):
                fmt_vars[pid] = pval.get("value", "?")
            else:
                fmt_vars[pid] = pval

        for pid, entry in self._entries.items():
            if not entry.semantic_guard:
                continue
            hint_template = entry.semantic_guard.get("prompt_hint", "")
            if not hint_template:
                continue
            try:
                hint = hint_template.format(**fmt_vars)
            except (KeyError, ValueError):
                hint = hint_template + " (데이터 미로드 — 값 설정 시 주의)"
            hints.append(f"- {pid}: {hint}")

        return "\n".join(hints)

    def all_ids(self) -> set[str]:
        """All registered parameter IDs."""
        return set(self._entries.keys())


# ── 모듈 레벨 캐시 (매 요청마다 YAML 재파싱 방지) ──
_catalog_cache: dict[str, ParameterCatalog] = {}


def get_catalog(domain: str) -> ParameterCatalog:
    """도메인별 ParameterCatalog 캐시 조회. YAML은 최초 1회만 파싱."""
    if domain not in _catalog_cache:
        _catalog_cache[domain] = ParameterCatalog(domain)
    return _catalog_cache[domain]
