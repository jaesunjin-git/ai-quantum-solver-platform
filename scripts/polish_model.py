import json, re

path = 'uploads/94/model.json'
with open(path, encoding='utf-8') as f:
    model = json.load(f)

# 1) is_night_duty -> is_night (변수명 통일)
model_str = json.dumps(model, ensure_ascii=False)
count_night = len(re.findall(r'is_night_duty(?!_trip)', model_str))
model_str = re.sub(r'is_night_duty(?!_trip)', 'is_night', model_str)
model = json.loads(model_str)

# 2) is_night 변수가 없으면 추가
var_ids = [v['id'] for v in model.get('variables', [])]
if 'is_night' not in var_ids:
    model['variables'].append({
        'id': 'is_night',
        'type': 'binary',
        'indices': ['J'],
        'description': 'duty j가 야간 근무인지 여부'
    })
    print('Added variable: is_night[J]')

# 3) big_m_constant, qualification 제거
remove_names = {'big_m_constant', 'qualification'}
old_count = len(model.get('constraints', []))
model['constraints'] = [c for c in model['constraints'] if c.get('name') not in remove_names]
removed = old_count - len(model['constraints'])

# 4) J size 조정 (96 -> 55)
for s in model.get('sets', []):
    if s['id'] == 'J' and s.get('size', 0) > 55:
        s['size'] = 55
        print(f'Set J: {s.get("size")} (was 96)')

with open(path, 'w', encoding='utf-8') as f:
    json.dump(model, f, ensure_ascii=False, indent=2)

# Summary
vars_list = model.get('variables', [])
cons = model.get('constraints', [])
hard = [c for c in cons if c.get('category', c.get('priority', '')) != 'soft']
soft = [c for c in cons if c.get('category', c.get('priority', '')) == 'soft']

print(f'\n=== FINAL MODEL ===')
print(f'Variables: {len(vars_list)} types')
for v in vars_list:
    print(f'  {v["id"]}: {v["type"]}, {v.get("indices",[])}')
print(f'Constraints: {len(cons)} (hard={len(hard)}, soft={len(soft)})')
print(f'Removed: {removed} ({remove_names})')
print(f'is_night_duty -> is_night: {count_night} replacements')
print(f'Sets:')
for s in model.get('sets', []):
    print(f'  {s["id"]}: size={s.get("size","N/A")}, source={s.get("source_column","N/A")}')
