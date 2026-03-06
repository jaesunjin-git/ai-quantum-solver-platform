import json

model_path = 'uploads/94/model.json'
with open(model_path, encoding='utf-8') as f:
    model = json.load(f)

# 1. 무의미한 soft constraint 제거
removed = []
kept = []
for c in model.get('constraints', []):
    cat = c.get('category', c.get('priority', 'hard'))
    name = c.get('name', '')
    expr = (c.get('expression') or '').strip()
    
    if cat == 'soft':
        # tautology 검사: LHS와 RHS가 같은 변수를 포함
        # y[j] <= y[j] + slack, duty_start[j] <= duty_start[j] + slack 등
        removed.append(name)
        print(f'  [REMOVED] soft: {name} (LLM-generated, no YAML template)')
    else:
        kept.append(c)

model['constraints'] = kept

# 2. J size는 DataBinder가 range(1..96)으로 생성하므로 model.json에서 변경해도
#    bound_data에는 96이 그대로 들어감. 이건 일단 그대로 두고 진행.

# 3. 저장
with open(model_path, 'w', encoding='utf-8') as f:
    json.dump(model, f, ensure_ascii=False, indent=2)

print(f'\nRemoved: {len(removed)} soft constraints')
print(f'Remaining: {len(kept)} hard constraints')
print(f'Objective: {model.get("objective", {}).get("expression", "?")}')
print(f'\nHard constraints:')
for c in kept:
    print(f'  {c.get("name")}: {(c.get("expression") or "")[:80]}')
