import json, yaml

# === 1) model.json의 expression을 YAML로 덮어쓰기 ===
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    ydata = yaml.safe_load(f)

ct = ydata.get('constraint_templates', {})
fixes = []

for c in model.get('constraints', []):
    name = c.get('name', '')
    yaml_info = ct.get(name, {})
    if isinstance(yaml_info, dict):
        yaml_expr = yaml_info.get('expression', '').strip()
        yaml_fe = yaml_info.get('for_each', '').strip()
        if yaml_expr:
            old_expr = c.get('expression', '')
            if old_expr != yaml_expr:
                c['expression'] = yaml_expr
                fixes.append(f'{name}: expression overwritten')
            if yaml_fe and c.get('for_each', '') != yaml_fe:
                c['for_each'] = yaml_fe
                fixes.append(f'{name}: for_each overwritten')

# === 2) is_night 변수 확인 ===
import re
model_str = json.dumps(model, ensure_ascii=False)
nd_count = len(re.findall(r'is_night_duty(?!_trip)', model_str))
if nd_count > 0:
    model_str = re.sub(r'is_night_duty(?!_trip)', 'is_night', model_str)
    model = json.loads(model_str)
    fixes.append(f'is_night_duty -> is_night: {nd_count}')

var_ids = [v['id'] for v in model.get('variables', [])]
if 'is_night' not in var_ids:
    model['variables'].append({
        'id': 'is_night', 'type': 'binary',
        'indices': ['J'], 'description': 'duty j 야간 근무 여부'
    })
    fixes.append('Added variable: is_night[J]')

# === 3) 불필요 제약 제거 ===
remove = {'big_m_constant', 'qualification'}
old_len = len(model['constraints'])
model['constraints'] = [c for c in model['constraints'] if c.get('name') not in remove]
if old_len != len(model['constraints']):
    fixes.append(f'Removed: {remove}')

# === 4) J size ===
for s in model.get('sets', []):
    if s['id'] == 'J' and s.get('size', 0) > 55:
        fixes.append(f'J: {s["size"]} -> 55')
        s['size'] = 55

# === 5) 저장 ===
with open('uploads/94/model.json', 'w', encoding='utf-8') as f:
    json.dump(model, f, ensure_ascii=False, indent=2)

# === 6) 요약 ===
cons = model['constraints']
hard = [c for c in cons if c.get('category', c.get('priority','')) != 'soft']
soft = [c for c in cons if c.get('category', c.get('priority','')) == 'soft']

print('=== FIXES ===')
for fix in fixes:
    print(f'  {fix}')
print(f'\n=== MODEL: vars={len(model["variables"])}, hard={len(hard)}, soft={len(soft)}, total={len(cons)} ===')

# === 7) 이 후처리를 math_model_generator.py에도 자동화 ===
# Gate2 직전에 YAML expression 덮어쓰기 로직 추가 필요
print('\n[TODO] math_model_generator.py에 자동 expression 덮어쓰기 추가 필요')
