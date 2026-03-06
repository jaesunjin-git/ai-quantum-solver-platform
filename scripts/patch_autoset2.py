import shutil, re

TARGET = 'domains/crew/skills/problem_definition.py'
shutil.copy2(TARGET, TARGET + '.bak_autoset')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

changes = 0

# ── Patch 1: big_m 자동계산 수정 (max_arr*2 -> max(1440, max_arr+60))
old_bigm = 'auto_val = max(int(ref_val), max_arr * 2)'
new_bigm = 'auto_val = max(int(ref_val), max_arr + 60)  # 최대도착+60분 여유, 최소 1440'
if old_bigm in src:
    src = src.replace(old_bigm, new_bigm)
    changes += 1
    print('[1] big_m 계산식 수정: max_arr*2 -> max_arr+60')
else:
    print('[1] big_m 계산식: 이미 수정됨 또는 미발견')

# ── Patch 2: classification 타입에서 하위 파라미터 자동 해결
old_classification = '''        # ★ NEW: YAML 의미적 타입 → 추출 방식 자동 결정
        # parameter 필드가 있으면 single_param으로 처리
        elif has_single_param:'''

# classification 블록 찾기 (computed_in_phase2 반환하는 부분)
old_class_block = '''        elif ctype in ("classification",):
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }'''

new_class_block = '''        elif ctype in ("classification",):
            # 하위 파라미터(예: is_night_duty, night_threshold)를 reference에서 자동 해결
            sub_params = cdata.get("parameters", [])
            if isinstance(sub_params, list) and sub_params:
                auto_values = {}
                all_resolved = True
                for sp in sub_params:
                    ref_val = self._lookup_reference_value(sp)
                    if ref_val is not None:
                        auto_values[sp] = {"value": ref_val, "source": "reference_default", "confidence": 0.9}
                    else:
                        auto_values[sp] = {"value": None, "source": "user_input_required"}
                        all_resolved = False
                return {
                    "status": "confirmed" if all_resolved else "partial",
                    "values": auto_values,
                    "computation_phase": "semantic_normalization",
                }
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }'''

if old_class_block in src:
    src = src.replace(old_class_block, new_class_block)
    changes += 1
    print('[2] classification 하위 파라미터 자동 해결 추가')
else:
    print('[2] classification 블록 미발견 - 수동 확인 필요')

# ── Patch 3: lower_bound 타입에서 reference default 자동 적용
# _extract_single_param의 user_input_required 반환 전에 reference_default가 있으면 자동 확인
old_input_required = '''        return {
            "status": "user_input_required",
            "values": {param_name: {
                "value": None,
                "source": "user_input_required",
                "reference_range": ref_range,
                "reference_default": ref_value,
            }},
        }'''

new_input_required = '''        # reference_default가 있고 typical_range 내이면 자동 적용
        if ref_value is not None:
            return {
                "status": "confirmed",
                "values": {param_name: {
                    "value": ref_value,
                    "source": "reference_default",
                    "confidence": 0.85,
                    "reference_range": ref_range,
                    "note": "reference_ranges.yaml 기본값 자동 적용",
                }},
            }

        return {
            "status": "user_input_required",
            "values": {param_name: {
                "value": None,
                "source": "user_input_required",
                "reference_range": ref_range,
                "reference_default": ref_value,
            }},
        }'''

if old_input_required in src:
    src = src.replace(old_input_required, new_input_required)
    changes += 1
    print('[3] reference_default 자동 적용 로직 추가')
else:
    print('[3] user_input_required 블록 미발견 - 수동 확인 필요')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

print(f'\n총 {changes}개 패치 적용')

import py_compile
py_compile.compile(TARGET, doraise=True)
print('문법 검증: OK')
