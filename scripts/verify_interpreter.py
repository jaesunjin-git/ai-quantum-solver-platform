import py_compile
py_compile.compile('engine/result_interpreter.py', doraise=True)
print('result_interpreter.py: syntax OK')

with open('engine/result_interpreter.py', encoding='utf-8') as f:
    content = f.read()

checks = {
    'y[j] active duty': 'solution.get("y", {})' in content and '활성 듀티' in content,
    'x[i,j] trip assign': '# x[i,j] = 트립' in content,
    'crew_assign simplified': 'duty_id = crew_id' in content or 'crew_assign[duty_id] = duty_id' in content,
}

print()
for name, ok in checks.items():
    print(f'  {name}: {"OK" if ok else "MISSING"}')
