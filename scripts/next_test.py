import json
import psycopg2
import subprocess
import time

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()

# 백업에서 전체 제약 로드
with open('uploads/94/all_constraints_backup.json', encoding='utf-8') as f:
    all_constraints = json.load(f)

with open('uploads/94/test_plan.json', encoding='utf-8') as f:
    plan = json.load(f)

test_sets = plan['test_sets']

# 현재 math_model 로드
cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
row = cur.fetchone()
base_model = json.loads(row[0])

# 이미 test 0 (trip_coverage) = OPTIMAL 확인됨
# test 1부터 시작
results = [{'test': 0, 'constraints': ['trip_coverage'], 'status': 'OPTIMAL', 'obj': 320.0}]

for test_idx in range(1, len(test_sets)):
    test_names = test_sets[test_idx]
    base_model['constraints'] = [c for c in all_constraints if c.get('name') in test_names]
    actual_names = [c.get('name') for c in base_model['constraints']]
    
    print(f'\n=== TEST {test_idx}: {len(actual_names)} constraints ===')
    print(f'  Added: {[n for n in test_names if n not in test_sets[test_idx-1]]}')
    print(f'  Total: {actual_names}')
    
    new_json = json.dumps(base_model, ensure_ascii=False)
    cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
    conn.commit()
    print(f'  DB updated. Run solver manually and report status.')
    print(f'  (constraints: {len(actual_names)})')
    
    # 저장
    results.append({
        'test': test_idx,
        'constraints': actual_names,
        'added': [n for n in test_names if n not in (test_sets[test_idx-1] if test_idx > 0 else [])],
        'status': 'PENDING'
    })
    
    with open('uploads/94/test_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # 한 번에 하나씩만 테스트
    break

cur.close()
conn.close()
print(f'\nTest {test_idx} ready. Restart server -> run solver -> share status.')
