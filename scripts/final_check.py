import json

# 1. model.json 최종 상태 확인
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

print('=== model.json final check ===')
for s in model.get('sets', []):
    print(f"  Set {s.get('id')}: source_type={s.get('source_type','?')}, size={s.get('size','?')}, source_file={s.get('source_file','?')}")

print(f"\nVariables: {len(model.get('variables', []))}")
for v in model.get('variables', []):
    print(f"  {v.get('id')}: {v.get('type')}, indices={v.get('indices')}")

print(f"\nConstraints: {len(model.get('constraints', []))}")
for c in model.get('constraints', []):
    cat = c.get('category', c.get('priority', 'hard'))
    expr = (c.get('expression') or '')[:80]
    print(f"  [{cat}] {c.get('name')}: {expr}")

print(f"\nObjective: {model.get('objective', {}).get('expression', '?')}")

# 2. solver 선택 로직 확인
print('\n=== Solver selection ===')
import os
for root, dirs, files in os.walk('engine'):
    if 'venv' in root:
        continue
    for fname in files:
        if fname.endswith('.py'):
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8') as f:
                    lines = f.readlines()
            except:
                continue
            for i, line in enumerate(lines):
                if 'compile_lp' in line or 'compile_cp_sat' in line or '_compile_lp' in line or '_compile_cp' in line:
                    if 'def ' not in line:  # 호출부만
                        print(f"  {fpath}:{i+1}: {line.rstrip()[:120]}")
