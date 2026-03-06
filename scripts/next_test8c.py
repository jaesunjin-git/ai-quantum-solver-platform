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

base_set = ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
            'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
            'day_duty_start', 'day_duty_end']

# TEST 8c: + night_duty_start
test_names = base_set + ['night_duty_start']
base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]

print(f'=== TEST 8c: + night_duty_start ===')
for c in all_constraints:
    if c.get('name') == 'night_duty_start':
        print(f'  expression: {c.get("expression")}')

new_json = json.dumps(base_model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
cur.close()
conn.close()
print('Restart server -> run solver')
