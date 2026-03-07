# domain_loader.py의 move_constraint에 안전장치 추가

with open('knowledge/domain_loader.py', encoding='utf-8') as f:
    content = f.read()

# move_constraint 메서드를 안전장치 포함 버전으로 교체
old_move = '''    def move_constraint(self, name: str, to_category: str) -> bool:
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
        return True'''

new_move = '''    def move_constraint(self, name: str, to_category: str, force: bool = False) -> dict:
        """
        제약의 카테고리를 변경 (hard↔soft).

        Returns dict:
          {"success": True/False, "warning": str or None, "needs_confirm": bool}

        안전장치:
          - fixed_category → 변경 불가
          - hard→soft: 경고 + 사용자 확인 필요 (제약 완화)
          - soft→hard: 경고 + 사용자 확인 필요 (infeasible 위험)
          - force=True면 경고 무시하고 즉시 변경
        """
        result = {"success": False, "warning": None, "needs_confirm": False}

        if not self.is_category_changeable(name):
            result["warning"] = f"'{name}'은(는) 구조적 필수 제약이므로 변경할 수 없습니다."
            return result

        from_cat = "hard" if name in self.hard_constraints else "soft" if name in self.soft_constraints else None
        if not from_cat:
            result["warning"] = f"'{name}' 제약을 찾을 수 없습니다."
            return result
        if from_cat == to_category:
            result["success"] = True  # 이미 해당 카테고리
            return result

        # 안전 경고 생성
        cdata = self.constraints[from_cat][name]
        desc = cdata.get("description", name)

        if from_cat == "hard" and to_category == "soft":
            result["warning"] = (
                f"⚠️ '{desc}'을(를) Hard → Soft로 변경하면 "
                f"이 제약이 위반되어도 해를 구할 수 있게 됩니다. "
                f"규정 위반이 허용 가능한 경우에만 변경하세요."
            )
            result["needs_confirm"] = True
        elif from_cat == "soft" and to_category == "hard":
            result["warning"] = (
                f"⚠️ '{desc}'을(를) Soft → Hard로 변경하면 "
                f"이 제약을 반드시 만족해야 합니다. "
                f"해가 존재하지 않을 수 있습니다 (INFEASIBLE 위험)."
            )
            result["needs_confirm"] = True

        if not force and result["needs_confirm"]:
            return result

        # 실제 이동
        cdata = self.constraints[from_cat].pop(name)
        cdata["_meta"]["original_category"] = cdata["_meta"].get("original_category", from_cat)
        cdata["_meta"]["user_changed"] = True
        cdata["_meta"]["changed_from"] = from_cat
        cdata["default_category"] = to_category
        self.constraints[to_category][name] = cdata
        result["success"] = True
        logger.info(f"Constraint '{name}' moved: {from_cat} -> {to_category} (user_changed)")
        return result

    def get_changeable_constraints(self) -> dict:
        """사용자가 변경 가능한 제약 목록 반환 (UI 표시용)"""
        result = {}
        for cat in ["hard", "soft"]:
            for name, cdata in self.constraints.get(cat, {}).items():
                if not isinstance(cdata, dict):
                    continue
                meta = cdata.get("_meta", {})
                if meta.get("changeable", True):
                    result[name] = {
                        "current_category": cat,
                        "description": cdata.get("description", name),
                        "changeable": True,
                        "user_changed": meta.get("user_changed", False),
                        "original_category": meta.get("original_category", cat),
                    }
        return result'''

content = content.replace(old_move, new_move)

with open('knowledge/domain_loader.py', 'w', encoding='utf-8') as f:
    f.write(content)

# 검증
import py_compile
py_compile.compile('knowledge/domain_loader.py', doraise=True)
print('domain_loader.py updated with safety guards - syntax OK')

# 기능 테스트
import sys, importlib
sys.path.insert(0, '.')
if 'knowledge.domain_loader' in sys.modules:
    del sys.modules['knowledge.domain_loader']
from knowledge.domain_loader import load_domain_knowledge

dk = load_domain_knowledge('railway', force_reload=True)

print(f'\\nHard: {len(dk.hard_constraints)}, Soft: {len(dk.soft_constraints)}')

# 테스트 1: fixed 제약 변경 시도
r = dk.move_constraint('trip_coverage', 'soft')
print(f'\\n1. trip_coverage → soft: success={r["success"]}, warning={r["warning"]}')

# 테스트 2: hard→soft (경고 + 확인 필요)
r = dk.move_constraint('mandatory_break', 'soft')
print(f'\\n2. mandatory_break → soft (no force):')
print(f'   success={r["success"]}, needs_confirm={r["needs_confirm"]}')
print(f'   warning={r["warning"]}')

# 테스트 3: hard→soft (force=True)
r = dk.move_constraint('mandatory_break', 'soft', force=True)
print(f'\\n3. mandatory_break → soft (force=True): success={r["success"]}')
print(f'   Hard: {len(dk.hard_constraints)}, Soft: {len(dk.soft_constraints)}')

# 테스트 4: soft→hard (경고)
r = dk.move_constraint('max_total_stay_time', 'hard')
print(f'\\n4. max_total_stay_time → hard (no force):')
print(f'   success={r["success"]}, needs_confirm={r["needs_confirm"]}')
print(f'   warning={r["warning"]}')

# 테스트 5: 변경 가능 목록
changeable = dk.get_changeable_constraints()
print(f'\\n5. Changeable constraints: {len(changeable)}')
for name, info in changeable.items():
    changed = ' (USER CHANGED)' if info['user_changed'] else ''
    print(f'   [{info["current_category"].upper():4s}] {name}{changed}')
