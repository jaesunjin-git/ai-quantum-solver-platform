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
base_model = json.loads(row[0])

# TEST 3a: test2 + preparation_time만
test_names = ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time']
base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]

print(f'=== TEST 3a: + preparation_time only ===')
print(f'  Constraints: {[c.get("name") for c in base_model["constraints"]]}')

# preparation_time의 expression 확인
for c in base_model['constraints']:
    if c.get('name') == 'preparation_time':
        print(f'  expression: {c.get("expression")}')
        print(f'  for_each: {c.get("for_each")}')

new_json = json.dumps(base_model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()

cur.close()
conn.close()
print(f'\nRestart server -> run solver -> share status.')
