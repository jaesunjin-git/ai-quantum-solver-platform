import json
import psycopg2

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()

with open('uploads/94/all_constraints_backup.json', encoding='utf-8') as f:
    all_constraints = json.load(f)

cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
row = cur.fetchone()
model = json.loads(row[0])

# 1. duty_start, duty_end를 integer로 변경 -> CP-SAT 경로 사용
for v in model.get('variables', []):
    if v.get('id') in ('duty_start', 'duty_end'):
        old_type = v.get('type')
        v['type'] = 'integer'
        print(f"[FIX] {v['id']}: {old_type} -> integer (enables CP-SAT)")

# 2. 전체 17개 제약 복원
model['constraints'] = all_constraints
print(f"\n[FIX] All {len(all_constraints)} constraints restored")

# 3. 확인
var_types = set(v.get('type') for v in model.get('variables', []))
print(f"Variable types: {var_types}")
print(f"Has continuous: {'continuous' in var_types} -> {'LP/MIP' if 'continuous' in var_types else 'CP-SAT'}")

new_json = json.dumps(model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
cur.close()
conn.close()

print(f"\nConstraints: {[c.get('name') for c in model['constraints']]}")
print('\nRestart server -> run solver')
