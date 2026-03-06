import shutil, py_compile

TARGET = 'domains/crew/skills/problem_definition.py'
shutil.copy2(TARGET, TARGET + '.bak_display')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

# 표시 로직에서 auto_model_variable인 파라미터는 "입력 필요"가 아닌 "자동 생성"으로 표시
old = '''            needs_input = {k: v for k, v in hard.items() if v.get("status") == "user_input_required"}'''
new = '''            # auto_model_variable은 입력 필요에서 제외
            needs_input = {}
            for k, v in hard.items():
                if v.get("status") != "user_input_required":
                    continue
                # 하위 값 중 auto_model_variable만 있으면 skip
                vals = v.get("values", {})
                has_real_missing = any(
                    sv.get("source") == "user_input_required" and sv.get("value") is None
                    for sv in vals.values()
                )
                if has_real_missing:
                    needs_input[k] = v'''

if old in src:
    src = src.replace(old, new)
    print('[1] needs_input 필터 수정: auto_model_variable 제외')
else:
    print('[1] needs_input 라인 미발견')

# classification이 confirmed인데 하위에 None이 있으면 표시에서 제외하지 않도록
# confirmed 항목의 auto_model_variable 값을 "(모델 변수)"로 표시
old2 = '''                    "source": "auto_model_variable"'''
new2 = '''                    "source": "auto_model_variable", "display": "(모델 자동 생성 변수)"'''

src = src.replace(old2, new2)
print('[2] auto_model_variable 표시 텍스트 추가')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print('문법 검증: OK')
