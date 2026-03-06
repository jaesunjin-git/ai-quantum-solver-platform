import csv

# 1. parameters.csv 전체 param_id 목록
print('=== parameters.csv: all param_ids ===')
with open('uploads/94/normalized/parameters.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f'Columns: {list(rows[0].keys())}')
print(f'Rows: {len(rows)}')
for row in rows:
    pid = row.get('param_id', row.get('id', row.get('parameter_id', '?')))
    val = row.get('value', row.get('val', '?'))
    print(f'  {pid} = {val}')

# 2. model.json 전체 constraint name + for_each + expression 앞 80자
import json
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

print(f'\n=== model.json constraints (name mapping check) ===')
for c in model.get('constraints', []):
    name = c.get('name', '?')
    fe = c.get('for_each', '')
    expr = c.get('expression', '')[:80]
    cat = c.get('category', c.get('priority', 'hard'))
    print(f'  [{cat}] {name}')
    print(f'    for_each: {fe}')
    print(f'    expression: {expr}')
