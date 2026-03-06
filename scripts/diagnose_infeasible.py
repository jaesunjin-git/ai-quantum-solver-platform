import json

# 1. overlap_pairs 실제 로딩 상태 확인
try:
    with open('uploads/94/normalized/overlap_pairs.json', encoding='utf-8') as f:
        op = json.load(f)
    print(f'=== overlap_pairs.json ===')
    print(f'Type: {type(op).__name__}, Length: {len(op)}')
    if len(op) > 0:
        print(f'Sample[0]: {op[0]}')
        print(f'Sample[-1]: {op[-1]}')
except Exception as e:
    print(f'overlap_pairs load error: {e}')

# 2. model.json의 sets 확인
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

print(f'\n=== model.json sets ===')
for s in model.get('sets', []):
    print(f"  {s.get('name')}: size={s.get('size')}, source={s.get('source','?')}")

# 3. model.json의 variables 확인
print(f'\n=== model.json variables ===')
for v in model.get('variables', []):
    print(f"  {v.get('name')}: {v.get('type')}, indices={v.get('indices')}")

# 4. 빈 expression 확인
print(f'\n=== Empty expressions ===')
for c in model.get('constraints', []):
    expr = c.get('expression', '').strip()
    if not expr:
        print(f"  [EMPTY] {c.get('name')} ({c.get('category','?')})")

# 5. parameters에서 trip_dep_time, trip_arr_time 확인
import csv
params = {}
with open('uploads/94/normalized/parameters.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        params[row.get('param_id', row.get('id', ''))] = row

print(f'\n=== Key parameters ===')
for key in ['big_m', 'max_driving_minutes', 'max_work_minutes', 'min_break_minutes',
            'preparation_minutes', 'cleanup_minutes', 'night_threshold',
            'day_duty_start_earliest', 'day_duty_end_latest', 'night_duty_start_earliest',
            'min_night_rest_minutes', 'min_night_sleep_minutes', 'max_total_stay_minutes',
            'max_wait_minutes', 'min_wait_minutes', 'max_single_wait_minutes',
            'min_meal_break_minutes']:
    if key in params:
        print(f"  {key} = {params[key].get('value', '?')}")
    else:
        print(f"  {key} = [NOT FOUND]")

# 6. trips.csv에서 trip_dep_time, trip_arr_time 컬럼 확인
with open('uploads/94/normalized/trips.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
print(f'\n=== trips.csv ===')
print(f'Columns: {list(rows[0].keys()) if rows else "empty"}')
print(f'Rows: {len(rows)}')
if rows:
    print(f'Sample[0]: { {k: rows[0][k] for k in list(rows[0].keys())[:8]} }')
    # trip_dep_time, trip_arr_time 존재 여부
    has_dep = 'trip_dep_time' in rows[0]
    has_arr = 'trip_arr_time' in rows[0]
    print(f'has trip_dep_time: {has_dep}')
    print(f'has trip_arr_time: {has_arr}')
    if not has_dep:
        # 비슷한 컬럼명 찾기
        for k in rows[0].keys():
            if 'dep' in k.lower() or 'start' in k.lower() or 'time' in k.lower():
                print(f'  candidate column: {k} = {rows[0][k]}')
    if not has_arr:
        for k in rows[0].keys():
            if 'arr' in k.lower() or 'end' in k.lower() or 'time' in k.lower():
                print(f'  candidate column: {k} = {rows[0][k]}')
