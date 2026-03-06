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

# TEST 9: base + 야간 전체 + day_night_classification + night_sleep_guarantee
test_names = ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
              'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
              'day_duty_start', 'day_duty_end', 'night_duty_start',
              'day_night_classification', 'night_sleep_guarantee']
base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]
actual = [c.get('name') for c in base_model['constraints']]

print(f'=== TEST 9: {len(actual)} constraints (all night constraints) ===')
print(f'  Added: day_night_classification, night_sleep_guarantee')
print(f'  Total: {actual}')

new_json = json.dumps(base_model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
cur.close()
conn.close()
print('Restart server -> run solver')
