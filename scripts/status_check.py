import json, psycopg2

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()
cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
model = json.loads(cur.fetchone()[0])

# 1. 현재 제약 목록
print('=== Current constraints ===')
for i, c in enumerate(model['constraints']):
    has_struct = 'lhs' in c and 'rhs' in c
    print(f'  {i+1}. {c["name"]} (struct={has_struct})')

# 2. 변수 확인
print('\n=== Variables ===')
for v in model.get('variables', []):
    print(f'  {v["id"]}: type={v.get("type")}, indices={v.get("indices")}, bounds=[{v.get("lower_bound","")},{v.get("upper_bound","")}]')

# 3. Sets 확인
print('\n=== Sets ===')
for s in model.get('sets', []):
    print(f'  {s["id"]}: source_type={s.get("source_type")}, size={s.get("size","")}')

# 4. Objective
print(f'\n=== Objective ===')
print(f'  {model.get("objective", {})}')

cur.close()
conn.close()

# 5. expression_parser 수정 상태 확인
print('\n=== expression_parser.py line 7 ===')
with open('engine/compiler/expression_parser.py', encoding='utf-8') as f:
    lines = f.readlines()
print(f'  {lines[6].rstrip()}')

# 6. ortools_compiler.py line 206 확인
print('\n=== ortools_compiler.py line 205-207 ===')
with open('engine/compiler/ortools_compiler.py', encoding='utf-8') as f:
    lines = f.readlines()
for i in range(204, 207):
    print(f'  {i+1}: {lines[i].rstrip()}')
