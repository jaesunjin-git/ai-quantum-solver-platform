import json, sys

pid = sys.argv[1] if len(sys.argv) > 1 else '129'

with open(f'uploads/{pid}/model.json', 'r', encoding='utf-8') as f:
    model = json.load(f)

print(f'=== Parameters with values ===')
bound = 0
unbound = 0
for p in model.get('parameters', []):
    pid_m = p.get('id', '')
    val = p.get('value', '')
    dv = p.get('default_value', p.get('default', ''))
    src = p.get('auto_bound_source', p.get('source_file', ''))
    effective = val if val else dv
    status = 'BOUND' if effective else 'EMPTY'
    if effective:
        bound += 1
    else:
        unbound += 1
    print(f'  [{status:5s}] {pid_m:45s} value={str(val):>6s}  default={str(dv):>6s}  src={src}')

print(f'\nBound: {bound}, Unbound: {unbound}, Total: {bound+unbound}')
