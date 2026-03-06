#!/usr/bin/env python3
"""
scripts/patch_problem_definition.py

problem_definition.py 패치:
1. _extract_constraint_value() - YAML 의미적 type 자동 매핑
2. soft weight - YAML weight 필드 우선 사용

실행: python scripts/patch_problem_definition.py
"""

import re

TARGET = "domains/crew/skills/problem_definition.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# ── 백업 ──
with open(TARGET + ".bak", "w", encoding="utf-8") as f:
    f.write(content)
print(f"[backup] {TARGET} -> {TARGET}.bak")

changes = 0

# ════════════════════════════════════════════════════════════
# 패치 1: _extract_constraint_value 교체
# ════════════════════════════════════════════════════════════

OLD_EXTRACT = '''    # ── Phase B: 타입별 값 추출 ──
    async def _extract_constraint_value(
        self, model, cname: str, cdata: dict, ctype: str,
        phase1_data: dict, state
    ) -> dict:
        """제약조건 타입에 따라 값을 추출한다."""

        if ctype == "single_param":
            return self._extract_single_param(cname, cdata, phase1_data)

        elif ctype == "compound":
            return self._extract_compound(cname, cdata, phase1_data)

        elif ctype == "conditional":
            return self._extract_conditional(cname, cdata, phase1_data)

        elif ctype == "pairwise":
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        elif ctype == "data_derived":
            return self._extract_data_derived(cname, cdata, phase1_data)

        return {"status": "unknown_type", "values": {}}'''

NEW_EXTRACT = '''    # ── Phase B: 타입별 값 추출 ──
    async def _extract_constraint_value(
        self, model, cname: str, cdata: dict, ctype: str,
        phase1_data: dict, state
    ) -> dict:
        """제약조건 타입에 따라 값을 추출한다."""

        # ★ CHANGED: YAML의 의미적 type → 추출 방식 매핑
        has_single_param = cdata.get("parameter") is not None
        has_compound_params = cdata.get("parameters") is not None

        # 직접 매핑되는 기존 타입
        if ctype == "single_param":
            return self._extract_single_param(cname, cdata, phase1_data)

        elif ctype == "compound":
            return self._extract_compound(cname, cdata, phase1_data)

        elif ctype == "conditional":
            return self._extract_conditional(cname, cdata, phase1_data)

        elif ctype == "pairwise":
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        elif ctype == "data_derived":
            return self._extract_data_derived(cname, cdata, phase1_data)

        # ★ NEW: YAML 의미적 타입 → 추출 방식 자동 결정
        # parameter 필드가 있으면 single_param으로 처리
        elif has_single_param:
            return self._extract_single_param(cname, cdata, phase1_data)

        # parameters (dict 또는 list)가 있으면 compound로 처리
        elif has_compound_params:
            params_raw = cdata.get("parameters")
            if isinstance(params_raw, list):
                converted = {}
                for p in params_raw:
                    if isinstance(p, str):
                        converted[p] = {"typical_range": cdata.get("typical_range", [])}
                cdata_copy = dict(cdata)
                cdata_copy["parameters"] = converted
                return self._extract_compound(cname, cdata_copy, phase1_data)
            return self._extract_compound(cname, cdata, phase1_data)

        # parameter 없는 구조적 제약 (equality, logical 등)
        elif ctype in ("equality", "logical"):
            return {
                "status": "confirmed",
                "values": {},
                "computation_phase": "compile_time",
            }

        # classification 등은 Phase 2에서 처리
        elif ctype in ("classification",):
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        return {"status": "unknown_type", "values": {}}'''

if OLD_EXTRACT in content:
    content = content.replace(OLD_EXTRACT, NEW_EXTRACT)
    changes += 1
    print("[patch1] _extract_constraint_value: OK")
else:
    print("[patch1] _extract_constraint_value: WARNING - exact match not found, trying flexible match")
    # 유연한 매칭: 핵심 패턴으로 찾기
    pattern = r'(    # ── Phase B: 타입별 값 추출 ──\n    async def _extract_constraint_value\b.*?return \{"status": "unknown_type", "values": \{\}\})'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        content = content.replace(match.group(1), NEW_EXTRACT)
        changes += 1
        print("[patch1] _extract_constraint_value: OK (flexible match)")
    else:
        print("[patch1] FAILED - please apply manually")

# ════════════════════════════════════════════════════════════
# 패치 2: soft weight 처리 교체
# ════════════════════════════════════════════════════════════

OLD_WEIGHT = '''            weight_range = cdata.get("weight_range", [0.1, 0.5])
            default_weight = round((weight_range[0] + weight_range[1]) / 2, 2)
            soft_results[cname] = {
                "name_ko": cdata.get("name_ko", cname),
                "type": cdata.get("type", "single_param"),
                "description": cdata.get("description", ""),
                "weight": default_weight,
                "weight_range": weight_range,
                "status": "default",
            }'''

NEW_WEIGHT = '''            # ★ CHANGED: YAML의 weight 필드를 우선 사용
            yaml_weight = cdata.get("weight")
            if yaml_weight is not None:
                default_weight = float(yaml_weight)
                weight_range = cdata.get("weight_range", [max(0.1, default_weight - 0.5), default_weight + 0.5])
            else:
                weight_range = cdata.get("weight_range", [0.1, 0.5])
                default_weight = round((weight_range[0] + weight_range[1]) / 2, 2)

            soft_results[cname] = {
                "name_ko": cdata.get("name_ko", cname),
                "type": cdata.get("type", "single_param"),
                "description": cdata.get("description", ""),
                "weight": default_weight,
                "weight_range": weight_range,
                "status": "default",
            }'''

if OLD_WEIGHT in content:
    content = content.replace(OLD_WEIGHT, NEW_WEIGHT)
    changes += 1
    print("[patch2] soft weight: OK")
else:
    print("[patch2] FAILED - please apply manually")

# ════════════════════════════════════════════════════════════
# 패치 3: _find_best_cdata_for_param의 detection_hints 호환성
# (기존 코드가 dict 형태 {"ko": [...]}를 기대하지만 실제는 list)
# ════════════════════════════════════════════════════════════

OLD_HINTS_CHECK = '''                this_hints = (cdef.get("detection_hints") or {}).get("ko", [])
                fall_hints = (fallback_cdata.get("detection_hints") or {}).get("ko", [])'''

NEW_HINTS_CHECK = '''                # ★ CHANGED: detection_hints가 list일 수도 dict일 수도 있음
                raw_this = cdef.get("detection_hints") or []
                this_hints = raw_this if isinstance(raw_this, list) else raw_this.get("ko", [])
                raw_fall = fallback_cdata.get("detection_hints") or []
                fall_hints = raw_fall if isinstance(raw_fall, list) else raw_fall.get("ko", [])'''

if OLD_HINTS_CHECK in content:
    content = content.replace(OLD_HINTS_CHECK, NEW_HINTS_CHECK)
    changes += 1
    print("[patch3] detection_hints compatibility: OK")
else:
    print("[patch3] detection_hints: not found (may already be compatible)")

# ── 저장 ──
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n✅ {changes}개 패치 적용 완료")
print(f"   백업: {TARGET}.bak")
print(f"\n📌 검증: python -c \"import py_compile; py_compile.compile('{TARGET}', doraise=True); print('OK')\"")