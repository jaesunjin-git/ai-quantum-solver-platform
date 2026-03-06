import shutil, py_compile

TARGET = 'engine/gates/gate2_model_validate.py'
shutil.copy2(TARGET, TARGET + '.bak_alias')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

changes = 0

# Patch 1: parameters가 list일 때 처리
old = '''                    # compound params
                    params_dict = cdef.get("parameters") or {}
                    if params_dict and hints_ko:
                        for pid in params_dict:'''

new = '''                    # compound params
                    params_raw = cdef.get("parameters") or {}
                    # list인 경우 dict로 변환
                    if isinstance(params_raw, list):
                        params_dict = {p: {} for p in params_raw if isinstance(p, str)}
                    else:
                        params_dict = params_raw
                    if params_dict and hints_ko:
                        for pid in params_dict:'''

if old in src:
    src = src.replace(old, new)
    changes += 1
    print('[1] parameters list/dict 호환 처리: OK')
else:
    print('[1] 대상 코드 미발견')

# Patch 2: detection_hints가 list일 때 처리 (dict의 'ko' 키 접근 실패 방지)
# hints_ko를 가져오는 부분 확인
old_hints = '''                    hints = cdef.get("detection_hints", {})
                    hints_ko = hints.get("ko", [])'''

new_hints = '''                    hints = cdef.get("detection_hints", {})
                    # detection_hints가 list이면 직접 사용
                    if isinstance(hints, list):
                        hints_ko = hints
                    else:
                        hints_ko = hints.get("ko", [])'''

if old_hints in src:
    src = src.replace(old_hints, new_hints)
    changes += 1
    print('[2] detection_hints list/dict 호환 처리: OK')
else:
    print('[2] detection_hints 코드 미발견 - 수동 확인 필요')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f'\n총 {changes}개 패치 적용, 문법 검증: OK')
