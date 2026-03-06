import json
import psycopg2

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()

cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
row = cur.fetchone()
model = json.loads(row[0])

# 전체 제약 백업
all_constraints = model['constraints'][:]
print(f'Total constraints: {len(all_constraints)}')
for c in all_constraints:
    print(f'  {c.get("name")}')

# 테스트 1: trip_coverage만 (가장 기본 - 각 trip이 정확히 1명에게 배정)
# 이것만으로 feasible하면 제약을 하나씩 추가
test_sets = [
    ['trip_coverage'],  # 기본
    ['trip_coverage', 'max_driving_time'],  # + 운전시간 제한
    ['trip_coverage', 'max_driving_time', 'max_work_time'],  # + 근무시간
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time'],  # + Big-M
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time', 
     'mandatory_break', 'meal_break_guarantee'],  # + 휴식
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
     'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time'],  # + 체재
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
     'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
     'max_wait_time'],  # + 대기시간
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
     'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
     'max_wait_time', 'min_wait_time', 'max_single_wait_time'],  # + overlap 대기
    ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
     'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
     'max_wait_time', 'min_wait_time', 'max_single_wait_time',
     'day_duty_start', 'day_duty_end', 'night_duty_start', 'day_night_classification',
     'night_sleep_guarantee'],  # 전체
]

# 첫 번째 테스트: trip_coverage만
test_names = test_sets[0]
model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]
print(f'\n=== TEST 1: {test_names} ({len(model["constraints"])} constraints) ===')

new_json = json.dumps(model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
print('[OK] DB updated with test constraints')
print('Restart server and run solver. If OPTIMAL, add more constraints.')

# 테스트 순서 저장
with open('uploads/94/test_plan.json', 'w', encoding='utf-8') as f:
    json.dump({
        'all_constraints': [c.get('name') for c in all_constraints],
        'test_sets': test_sets,
        'current_test': 0,
    }, f, ensure_ascii=False, indent=2)

# 전체 제약도 백업
with open('uploads/94/all_constraints_backup.json', 'w', encoding='utf-8') as f:
    json.dump(all_constraints, f, ensure_ascii=False, indent=2)

print('Test plan saved to uploads/94/test_plan.json')
print('Full constraints backed up to uploads/94/all_constraints_backup.json')

cur.close()
conn.close()
