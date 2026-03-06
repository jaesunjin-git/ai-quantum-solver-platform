#!/usr/bin/env python3
"""
scripts/patch_reference_range.py

problem_definition.py 패치:
1. _extract_single_param - user_input_required 시 reference_range 포함
2. _extract_compound - 동일 처리

실행: python scripts/patch_reference_range.py
"""

TARGET = "domains/crew/skills/problem_definition.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

with open(TARGET + ".bak2", "w", encoding="utf-8") as f:
    f.write(content)
print(f"[backup] {TARGET} -> {TARGET}.bak2")

changes = 0

# ════════════════════════════════════════════════════════════
# 패치 1: _extract_single_param - reference_range 추가
# ════════════════════════════════════════════════════════════

OLD_SINGLE = '''    def _extract_single_param(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        param_name = cdata.get("parameter")
        if not param_name:
            return {"status": "confirmed", "values": {}}

        # Phase 1 파라미터에서 검색
        value = self._search_phase1_params(param_name, cdata, phase1_data)
        if value is not None:
            return {
                "status": "extracted",
                "values": {param_name: {"value": value, "source": "phase1_data", "confidence": 0.8}},
            }

        return {
            "status": "user_input_required",
            "values": {param_name: {"value": None, "source": "user_input_required"}},
        }'''

NEW_SINGLE = '''    def _extract_single_param(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        param_name = cdata.get("parameter")
        if not param_name:
            return {"status": "confirmed", "values": {}}

        # Phase 1 파라미터에서 검색
        value = self._search_phase1_params(param_name, cdata, phase1_data)
        if value is not None:
            return {
                "status": "extracted",
                "values": {param_name: {"value": value, "source": "phase1_data", "confidence": 0.8}},
            }

        # ★ NEW: reference_ranges에서 참고 범위 및 기본값 조회
        ref_range = cdata.get("typical_range")
        ref_value = self._lookup_reference_value(param_name)

        return {
            "status": "user_input_required",
            "values": {param_name: {
                "value": None,
                "source": "user_input_required",
                "reference_range": ref_range,
                "reference_default": ref_value,
            }},
        }'''

if OLD_SINGLE in content:
    content = content.replace(OLD_SINGLE, NEW_SINGLE)
    changes += 1
    print("[patch1] _extract_single_param: OK")
else:
    print("[patch1] FAILED - _extract_single_param not found")

# ════════════════════════════════════════════════════════════
# 패치 2: _lookup_reference_value 메서드 추가
# (기존 _find_best_cdata_for_param 바로 위에 삽입)
# ════════════════════════════════════════════════════════════

LOOKUP_METHOD = '''
    # ★ NEW: reference_ranges.yaml에서 기본값 조회
    def _lookup_reference_value(self, param_name: str):
        """reference_ranges.yaml에서 첫 번째 매칭되는 기본값 반환"""
        if not hasattr(self, '_reference_cache'):
            self._reference_cache = self._load_reference_ranges()
        return self._reference_cache.get(param_name)

    def _load_reference_ranges(self) -> dict:
        """모든 reference_ranges.yaml에서 파라미터 기본값 수집"""
        import os, yaml
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        domains_dir = os.path.join(base, "knowledge", "domains")
        values = {}
        if not os.path.isdir(domains_dir):
            return values
        for dname in os.listdir(domains_dir):
            rpath = os.path.join(domains_dir, dname, "reference_ranges.yaml")
            if not os.path.isfile(rpath):
                continue
            try:
                with open(rpath, "r", encoding="utf-8") as f:
                    rdata = yaml.safe_load(f) or {}
            except Exception:
                continue
            # 첫 번째 region의 values를 기본값으로 사용
            for region_key, region in rdata.items():
                if isinstance(region, dict) and "values" in region:
                    for k, v in region["values"].items():
                        if k not in values:  # 첫 번째 값 우선
                            values[k] = v
        return values

'''

INSERT_BEFORE = '    def _find_best_cdata_for_param'

if INSERT_BEFORE in content and '_lookup_reference_value' not in content:
    content = content.replace(INSERT_BEFORE, LOOKUP_METHOD + INSERT_BEFORE)
    changes += 1
    print("[patch2] _lookup_reference_value: OK")
elif '_lookup_reference_value' in content:
    print("[patch2] _lookup_reference_value: already exists, skipped")
else:
    print("[patch2] FAILED - insertion point not found")

# ════════════════════════════════════════════════════════════
# 패치 3: _format_proposal에서 reference 정보 표시 개선
# ════════════════════════════════════════════════════════════

OLD_FORMAT = '''            if needs_input:
                lines.append("**❓ 입력 필요 (Hard):**")
                for cname, cdata in needs_input.items():
                    name_ko = cdata.get("name_ko", cname)
                    desc = cdata.get("description", "")
                    lines.append(f"- **{name_ko}**: {desc}")
                    for pname, pval in cdata.get("values", {}).items():
                        ref = pval.get("reference_range")
                        if ref:
                            lines.append(f"  - {pname}: 참고 범위 {ref}")
                        else:
                            lines.append(f"  - {pname}: ???")
                lines.append("")'''

NEW_FORMAT = '''            if needs_input:
                lines.append("**❓ 입력 필요 (Hard):**")
                for cname, cdata in needs_input.items():
                    name_ko = cdata.get("name_ko", cname)
                    desc = cdata.get("description", "")
                    lines.append(f"- **{name_ko}**: {desc}")
                    for pname, pval in cdata.get("values", {}).items():
                        ref = pval.get("reference_range")
                        ref_default = pval.get("reference_default")
                        if ref and ref_default is not None:
                            lines.append(f"  - `{pname}` = ??? (참고 범위: {ref}, 기본값: {ref_default})")
                        elif ref:
                            lines.append(f"  - `{pname}` = ??? (참고 범위: {ref})")
                        elif ref_default is not None:
                            lines.append(f"  - `{pname}` = ??? (기본값: {ref_default})")
                        else:
                            lines.append(f"  - `{pname}` = ???")
                lines.append("")'''

if OLD_FORMAT in content:
    content = content.replace(OLD_FORMAT, NEW_FORMAT)
    changes += 1
    print("[patch3] _format_proposal reference display: OK")
else:
    print("[patch3] FAILED - format section not found")

# ── 저장 ──
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n✅ {changes}개 패치 적용 완료")
print(f"   백업: {TARGET}.bak2")