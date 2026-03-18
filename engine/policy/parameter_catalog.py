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
            )
            self._entries[pid] = entry

            # Build alias → canonical map
            for alias in entry.aliases:
                self._alias_map[alias] = pid

            # Build forbidden pairs (bidirectional)
            for forbidden in entry.not_alias_of:
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
            lo, hi = float(vr[0]), float(vr[1])
            if v < lo or v > hi:
                return (
                    f"Parameter '{name}' = {v}: valid_range [{lo}, {hi}] 벗어남 "
                    f"(family={entry.family}, role={entry.semantic_role})"
                )
        return None

    def get_entry(self, name: str) -> Optional[CatalogEntry]:
        """Direct entry lookup (no alias resolution)."""
        return self._entries.get(name)

    def all_ids(self) -> set[str]:
        """All registered parameter IDs."""
        return set(self._entries.keys())
