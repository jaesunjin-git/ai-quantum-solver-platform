import shutil, py_compile

# ── Patch 1: Gate2 detection_hints list 호환 ──
TARGET1 = 'engine/gates/gate2_model_validate.py'
shutil.copy2(TARGET1, TARGET1 + '.bak_hints')

with open(TARGET1, encoding='utf-8') as f:
    src1 = f.read()

c1 = 0

old_hints = '                    hints_ko = (cdef.get("detection_hints") or {}).get("ko", [])'
new_hints = '''                    _raw_hints = cdef.get("detection_hints") or {}
                    if isinstance(_raw_hints, list):
                        hints_ko = _raw_hints
                    elif isinstance(_raw_hints, dict):
                        hints_ko = _raw_hints.get("ko", [])
                    else:
                        hints_ko = []'''

if old_hints in src1:
    src1 = src1.replace(old_hints, new_hints)
    c1 += 1
    print('[1] Gate2 detection_hints list/dict 호환: OK')
else:
    print('[1] detection_hints 라인 미발견')

with open(TARGET1, 'w', encoding='utf-8') as f:
    f.write(src1)
py_compile.compile(TARGET1, doraise=True)
print(f'    Gate2: {c1}개 패치, 문법 OK')


# ── Patch 2: math_model.py - data column 파라미터를 user_input에서 제외 ──
TARGET2 = 'domains/crew/skills/math_model.py'
shutil.copy2(TARGET2, TARGET2 + '.bak_uip')

with open(TARGET2, encoding='utf-8') as f:
    src2 = f.read()

c2 = 0

# user_input_required 체크 시, source_file이 있는 파라미터는 제외
old_uip = '''            # ★ user_input_required 파라미터 체크
            need_input_params = [
                p.get("id", p.get("name", ""))
                for p in model.get("parameters", [])
                if p.get("user_input_required")
            ]'''

new_uip = '''            # ★ user_input_required 파라미터 체크
            # source_file/source_column이 있는 파라미터는 데이터에서 바인딩 가능하므로 제외
            need_input_params = [
                p.get("id", p.get("name", ""))
                for p in model.get("parameters", [])
                if p.get("user_input_required")
                and not p.get("source_file")
                and not p.get("source_column")
            ]'''

if old_uip in src2:
    src2 = src2.replace(old_uip, new_uip)
    c2 += 1
    print('[2] user_input_required 필터 (data column 제외): OK')
else:
    print('[2] user_input_required 블록 미발견')

with open(TARGET2, 'w', encoding='utf-8') as f:
    f.write(src2)
py_compile.compile(TARGET2, doraise=True)
print(f'    math_model: {c2}개 패치, 문법 OK')

print(f'\n총 {c1+c2}개 패치 완료')
