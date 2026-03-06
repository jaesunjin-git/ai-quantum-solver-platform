import json
import shutil

# 원본 복원
shutil.copy('uploads/94/normalized/overlap_pairs_original.json', 'uploads/94/normalized/overlap_pairs.json')

with open('uploads/94/normalized/overlap_pairs.json', encoding='utf-8') as f:
    pairs = json.load(f)
print(f'[OK] overlap_pairs.json restored: {len(pairs)} pairs')

# 대신 전체 17개 중 overlap 관련 3개를 제외한 14개로 먼저 완성 테스트
# overlap 제약(min_wait_time, max_single_wait_time)은 컴파일이 오래 걸리므로
# max_wait_time과 함께 나중에 최적화 후 추가

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

# 14개 제약 (overlap 3개 제외)
test_names = ['trip_coverage', 'max_driving_time', 'max_work_time', 'preparation_time', 'cleanup_time',
              'mandatory_break', 'meal_break_guarantee', 'night_rest', 'max_total_stay_time',
              'day_duty_start', 'day_duty_end', 'night_duty_start',
              'day_night_classification', 'night_sleep_guarantee']
base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]
actual = [c.get('name') for c in base_model['constraints']]

print(f'\n=== FINAL TEST: {len(actual)} constraints (without overlap/wait) ===')
print(f'Total: {actual}')

new_json = json.dumps(base_model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
cur.close()
conn.close()
print('\nRestart server -> run solver')
