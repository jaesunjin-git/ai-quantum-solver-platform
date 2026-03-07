# domain_loader.py 수정 스크립트
# v3 YAML 호환 + 자동 hard/soft 분리 + 메타데이터 보존

with open('knowledge/domain_loader.py', encoding='utf-8') as f:
    content = f.read()

# 백업
import shutil
shutil.copy2('knowledge/domain_loader.py', 'knowledge/domain_loader.py.bak')

new_content = '''"""
Domain Knowledge Loader (v3 호환)

YAML 구조:
  v2: hard: {...}, soft: {...}  (분리형)
  v3: constraints: {name: {default_category: hard/soft, ...}}  (통합형)

DomainKnowledge는 두 형식 모두 지원하며,
외부 인터페이스(dk.hard_constraints, dk.soft_constraints)는 동일하게 유지.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent


def _safe_load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"YAML load error: {path} - {e}")
        return {}


def _split_by_category(unified_constraints: dict) -> dict:
    """
    v3 통합 constraints를 hard/soft 딕셔너리로 분리.
    각 제약에 _meta 필드를 추가하여 원본 정보 보존.
    """
    hard = {}
    soft = {}
    for name, cdata in unified_constraints.items():
        if not isinstance(cdata, dict):
            continue
        category = cdata.get("default_category", "hard")
        fixed = cdata.get("fixed_category", False)

        # 메타데이터 주입 (원본 보존)
        enriched = dict(cdata)
        enriched["_meta"] = {
            "original_category": category,
            "fixed_category": fixed,
            "changeable": not fixed,
        }

        if category == "soft":
            soft[name] = enriched
        else:
            hard[name] = enriched

    return {"hard": hard, "soft": soft}


def _detect_yaml_version(data: dict) -> str:
    """YAML 버전 감지: v2(분리형) vs v3(통합형)"""
    if "constraints" in data and isinstance(data["constraints"], dict):
        sample = next(iter(data["constraints"].values()), None)
        if isinstance(sample, dict) and "default_category" in sample:
            return "v3"
    if "hard" in data and isinstance(data.get("hard"), dict):
        return "v2"
    return "unknown"


@dataclass
class DomainKnowledge:
    """도메인 지식 번들. v2/v3 YAML 모두 동일한 인터페이스."""
    domain_name: str = ""
    index: Dict = field(default_factory=dict)
    constraints: Dict = field(default_factory=dict)  # {"hard": {...}, "soft": {...}}
    templates: Dict = field(default_factory=dict)
    reference_ranges: Dict = field(default_factory=dict)
    raw_single: Optional[Dict] = None
    _yaml_version: str = "unknown"
    _unified_constraints: Dict = field(default_factory=dict)  # v3 원본 보존

    @property
    def detection_keywords(self) -> list:
        return self.index.get("detection_keywords", [])

    @property
    def sub_domains(self) -> dict:
        return self.index.get("sub_domains", {})

    @property
    def network_topologies(self) -> dict:
        return self.index.get("network_topologies", {})

    @property
    def hard_constraints(self) -> dict:
        return self.constraints.get("hard", {})

    @property
    def soft_constraints(self) -> dict:
        return self.constraints.get("soft", {})

    @property
    def category_rules(self) -> dict:
        """v3 자동 분류 규칙 반환"""
        return self._unified_constraints.get("_category_rules", {})

    @property
    def yaml_version(self) -> str:
        return self._yaml_version

    def get_constraint(self, name: str) -> Optional[dict]:
        return self.hard_constraints.get(name) or self.soft_constraints.get(name)

    def get_constraint_meta(self, name: str) -> Optional[dict]:
        """제약의 메타데이터 반환 (fixed_category, changeable 등)"""
        c = self.get_constraint(name)
        if c:
            return c.get("_meta", {})
        return None

    def is_category_changeable(self, name: str) -> bool:
        """사용자가 hard/soft를 변경할 수 있는 제약인지"""
        meta = self.get_constraint_meta(name)
        return meta.get("changeable", True) if meta else True

    def get_reference_range(self, sub_domain: str, param: str) -> Optional[dict]:
        sd = self.reference_ranges.get(sub_domain, {})
        return sd.get(param)

    def all_constraint_names(self) -> List[str]:
        return list(self.hard_constraints.keys()) + list(self.soft_constraints.keys())

    def constraints_by_type(self, ctype: str) -> Dict[str, dict]:
        result = {}
        for name, cdata in self.hard_constraints.items():
            if isinstance(cdata, dict) and cdata.get("type") == ctype:
                result[name] = cdata
        for name, cdata in self.soft_constraints.items():
            if isinstance(cdata, dict) and cdata.get("type") == ctype:
                result[name] = cdata
        return result

    def move_constraint(self, name: str, to_category: str) -> bool:
        """
        제약의 카테고리를 변경 (hard↔soft).
        fixed_category인 제약은 변경 불가.
        Returns True if moved, False if not changeable.
        """
        if not self.is_category_changeable(name):
            logger.warning(f"Constraint '{name}' has fixed_category, cannot move")
            return False

        from_cat = "hard" if name in self.hard_constraints else "soft" if name in self.soft_constraints else None
        if not from_cat or from_cat == to_category:
            return False

        cdata = self.constraints[from_cat].pop(name)
        cdata["_meta"]["original_category"] = cdata["_meta"].get("original_category", from_cat)
        cdata["default_category"] = to_category
        self.constraints[to_category][name] = cdata
        logger.info(f"Constraint '{name}' moved: {from_cat} -> {to_category}")
        return True


# ── 캐시 ──
_cache: Dict[str, DomainKnowledge] = {}


def load_domain_knowledge(domain_name: str, force_reload: bool = False) -> DomainKnowledge:
    """
    도메인 지식을 로드한다.
    1) knowledge/domains/{domain_name}/ 폴더가 있으면 분리형으로 로드
    2) 없으면 knowledge/domains/{domain_name}.yaml 단일 파일 로드
    3) 둘 다 없으면 빈 DomainKnowledge 반환

    v2 YAML (hard/soft 분리) 와 v3 YAML (통합 constraints) 모두 지원.
    """
    if domain_name in _cache and not force_reload:
        return _cache[domain_name]

    domains_dir = _BASE / "domains"
    dk = DomainKnowledge(domain_name=domain_name)

    # 방법 1: 폴더 구조
    folder = domains_dir / domain_name
    if folder.is_dir():
        dk.index = _safe_load_yaml(folder / "_index.yaml")
        raw_constraints = _safe_load_yaml(folder / "constraints.yaml")
        dk.templates = _safe_load_yaml(folder / "templates.yaml")
        dk.reference_ranges = _safe_load_yaml(folder / "reference_ranges.yaml")

        # YAML 버전 감지 및 처리
        version = _detect_yaml_version(raw_constraints)
        dk._yaml_version = version

        if version == "v3":
            # v3: 통합 constraints → 자동 분리
            unified = raw_constraints.get("constraints", {})
            dk._unified_constraints = {
                **unified,
                "_category_rules": raw_constraints.get("category_rules", {}),
            }
            dk.constraints = _split_by_category(unified)
            logger.info(
                f"DomainLoader: loaded '{domain_name}' (folder, v3) - "
                f"hard={len(dk.hard_constraints)}, soft={len(dk.soft_constraints)}, "
                f"total={len(unified)}"
            )
        elif version == "v2":
            # v2: 이미 분리된 구조 그대로 사용
            dk.constraints = {
                "hard": raw_constraints.get("hard", {}),
                "soft": raw_constraints.get("soft", {}),
            }
            logger.info(
                f"DomainLoader: loaded '{domain_name}' (folder, v2) - "
                f"hard={len(dk.hard_constraints)}, soft={len(dk.soft_constraints)}"
            )
        else:
            dk.constraints = raw_constraints
            logger.warning(f"DomainLoader: unknown YAML version for '{domain_name}'")

        _cache[domain_name] = dk
        return dk

    # 방법 2: 단일 파일
    single_file = domains_dir / f"{domain_name}.yaml"
    if single_file.exists():
        raw = _safe_load_yaml(single_file)
        dk.raw_single = raw
        dk.index = {
            "detection_keywords": raw.get("detection_keywords", []),
            "sub_domains": raw.get("sub_domains", {}),
            "network_topologies": raw.get("network_topologies", {}),
        }
        dk.constraints = raw.get("constraints", {})
        dk.templates = {"constraint_templates": raw.get("constraint_templates", {})}
        dk.reference_ranges = raw.get("reference_values", {})
        dk._yaml_version = "single"
        logger.info(
            f"DomainLoader: loaded '{domain_name}' (single file) - "
            f"hard={len(dk.hard_constraints)}, soft={len(dk.soft_constraints)}"
        )
        _cache[domain_name] = dk
        return dk

    # 방법 3: 없음
    logger.warning(f"DomainLoader: domain '{domain_name}' not found")
    _cache[domain_name] = dk
    return dk


def detect_domain_from_keywords(search_text: str) -> Optional[str]:
    """주어진 텍스트에서 도메인을 감지한다."""
    domains_dir = _BASE / "domains"
    if not domains_dir.exists():
        return None

    search_lower = search_text.lower()
    best_domain = None
    best_score = 0

    for entry in domains_dir.iterdir():
        if entry.is_dir():
            idx = _safe_load_yaml(entry / "_index.yaml")
            keywords = idx.get("detection_keywords", [])
            domain_name = entry.name
        elif entry.is_file() and entry.suffix == ".yaml":
            raw = _safe_load_yaml(entry)
            keywords = raw.get("detection_keywords", [])
            domain_name = entry.stem
        else:
            continue

        score = 0
        for group in keywords:
            if isinstance(group, list):
                if any(kw.lower() in search_lower for kw in group):
                    score += 1
            elif isinstance(group, str):
                if group.lower() in search_lower:
                    score += 1

        if score > best_score:
            best_score = score
            best_domain = domain_name

    return best_domain if best_score > 0 else None


def list_available_domains() -> List[str]:
    """사용 가능한 도메인 목록 반환."""
    domains_dir = _BASE / "domains"
    if not domains_dir.exists():
        return []

    result = set()
    for entry in domains_dir.iterdir():
        if entry.is_dir() and not entry.name.startswith("_"):
            result.add(entry.name)
        elif entry.is_file() and entry.suffix == ".yaml":
            result.add(entry.stem)
    return sorted(result)
'''

with open('knowledge/domain_loader.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

# 검증
import py_compile
py_compile.compile('knowledge/domain_loader.py', doraise=True)
print('domain_loader.py updated and syntax OK')

# 기능 테스트
import sys
sys.path.insert(0, '.')
# 캐시 클리어 후 재로드
import importlib
if 'knowledge.domain_loader' in sys.modules:
    del sys.modules['knowledge.domain_loader']

from knowledge.domain_loader import load_domain_knowledge, _detect_yaml_version, _split_by_category

dk = load_domain_knowledge('railway', force_reload=True)
print(f'\nYAML version: {dk.yaml_version}')
print(f'Hard constraints: {len(dk.hard_constraints)}')
print(f'Soft constraints: {len(dk.soft_constraints)}')
print(f'Total: {len(dk.hard_constraints) + len(dk.soft_constraints)}')
print()

print('Hard:')
for name in dk.hard_constraints:
    meta = dk.get_constraint_meta(name)
    fixed = meta.get('fixed_category', False) if meta else False
    changeable = dk.is_category_changeable(name)
    print(f'  {name}: fixed={fixed}, changeable={changeable}')

print('\\nSoft:')
for name in dk.soft_constraints:
    meta = dk.get_constraint_meta(name)
    fixed = meta.get('fixed_category', False) if meta else False
    changeable = dk.is_category_changeable(name)
    print(f'  {name}: fixed={fixed}, changeable={changeable}')

# move_constraint 테스트
print('\\n=== move_constraint test ===')
print(f'Move trip_coverage to soft: {dk.move_constraint("trip_coverage", "soft")}')  # fixed → False
print(f'Move max_total_stay_time to hard: {dk.move_constraint("max_total_stay_time", "hard")}')  # changeable → True
print(f'Hard after move: {len(dk.hard_constraints)}')
print(f'Soft after move: {len(dk.soft_constraints)}')
