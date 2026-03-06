import json
import psycopg2

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()

with open('uploads/94/all_constraints_backup.json', encoding='utf-8') as f:
    all_constraints = json.load(f)

with open('uploads/94/test_plan.json', encoding='utf-8') as f:
    plan = json.load(f)

test_sets = plan['test_sets']

cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
row = cur.fetchone()
base_model = json.loads(row[0])

# TEST 2: + max_work_time
test_idx = 2
test_names = test_sets[test_idx]
base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]
actual_names = [c.get('name') for c in base_model['constraints']]
added = [n for n in test_names if n not in test_sets[test_idx-1]]

print(f'=== TEST {test_idx}: {len(actual_names)} constraints ===')
print(f'  Added: {added}')
print(f'  Total: {actual_names}')

new_json = json.dumps(base_model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()

cur.close()
conn.close()
print(f'\nRestart server -> run solver -> share status.')
