import shutil, py_compile

TARGET = 'domains/crew/skills/problem_definition.py'
shutil.copy2(TARGET, TARGET + '.bak_order')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

# classification 블록을 has_compound_params 앞으로 이동
old_order = '''        # ★ NEW: YAML 의미적 타입 → 추출 방식 자동 결정
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

        # classification: 주간/야간 구분 등 자동 세팅
        elif ctype in ("classification",):'''

new_order = '''        # ★ NEW: YAML 의미적 타입 → 추출 방식 자동 결정

        # classification: 주간/야간 구분 등 자동 세팅 (compound보다 먼저 체크)
        elif ctype in ("classification",):'''

# classification 블록 본문은 그대로 유지하고, 그 뒤에 나머지를 붙임
old_after_class = '''            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        # constant 타입 (big_m 등): 자동 세팅'''

new_after_class = '''            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

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

        # constant 타입 (big_m 등): 자동 세팅'''

if old_order in src:
    # 1단계: compound/equality 블록 제거하고 classification을 먼저 배치
    src = src.replace(old_order, new_order)
    # 2단계: classification 뒤에 compound/equality/single_param 블록 복원
    src = src.replace(old_after_class, new_after_class)
    print('[OK] 분기 순서 변경: classification -> single_param -> compound -> equality -> constant')
else:
    print('[FAIL] 기존 코드 블록 미발견 - 수동 확인 필요')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print('문법 검증: OK')
