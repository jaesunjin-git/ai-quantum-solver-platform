import csv, json

# 1. parameters.csv - semantic_id와 value 매핑
print('=== parameters.csv: semantic_id -> value ===')
with open('uploads/94/normalized/parameters.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

for row in rows:
    sid = row.get('semantic_id', '')
    pname = row.get('param_name', '')
    val = row.get('value', '')
    print(f'  {sid:40s} | {val:10s} | {pname}')

# 2. model.json sets - 한글 이름 확인
print('\n=== model.json sets (raw) ===')
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)
for s in model.get('sets', []):
    print(f'  {json.dumps(s, ensure_ascii=False)}')

# 3. model.json variables (raw)
print('\n=== model.json variables (raw) ===')
for v in model.get('variables', []):
    print(f'  {json.dumps(v, ensure_ascii=False)}')

# 4. model.json objective
print('\n=== model.json objective ===')
obj = model.get('objective', {})
print(f'  {json.dumps(obj, ensure_ascii=False)[:200]}')
