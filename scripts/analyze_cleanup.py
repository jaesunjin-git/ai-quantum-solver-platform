import json

# 1. trip_arr_time 범위 확인
import csv
with open('uploads/94/normalized/trips.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

arr_times = [int(r['trip_arr_time']) for r in rows]
dep_times = [int(r['trip_dep_time']) for r in rows]

print(f'=== Trip times ===')
print(f'  dep_time: min={min(dep_times)}, max={max(dep_times)}')
print(f'  arr_time: min={min(arr_times)}, max={max(arr_times)}')

# 2. cleanup_time 제약 분석
# duty_end[j] >= trip_arr_time[i] + cleanup_minutes - big_m * (1 - x[i,j])
# x[i,j]=1: duty_end[j] >= arr_time[i] + 30
# x[i,j]=0: duty_end[j] >= arr_time[i] + 30 - 1440

print(f'\n=== cleanup_time constraint analysis ===')
print(f'  When x=1: duty_end[j] >= max(arr_time) + cleanup = {max(arr_times)} + 30 = {max(arr_times) + 30}')
print(f'  duty_end upper_bound = 1440')
print(f'  Is {max(arr_times) + 30} <= 1440? {max(arr_times) + 30 <= 1440}')

print(f'\n  When x=0: duty_end[j] >= arr_time[i] + 30 - 1440')
print(f'  Worst case: {min(arr_times)} + 30 - 1440 = {min(arr_times) + 30 - 1440} (negative, OK)')

# 3. preparation_time과 비교
# preparation_time: duty_start[j] <= trip_dep_time[i] - preparation_minutes + big_m * (1 - x[i,j])
# x=1: duty_start[j] <= dep_time[i] - 40
# x=0: duty_start[j] <= dep_time[i] - 40 + 1440
print(f'\n=== preparation_time constraint analysis ===')
print(f'  When x=1: duty_start[j] <= min(dep_time) - prep = {min(dep_times)} - 40 = {min(dep_times) - 40}')
print(f'  duty_start lower_bound = 0')
print(f'  Is 0 <= {min(dep_times) - 40}? {0 <= min(dep_times) - 40}')

# 4. 핵심: preparation + cleanup 함께일 때
# crew j가 trip i를 수행하면:
#   duty_start[j] <= dep_time[i] - 40
#   duty_end[j] >= arr_time[i] + 30
# + max_work_time: prep*y[j] + sum(duration*x) + cleanup*y[j] <= 660
# 
# 문제: 한 crew가 여러 trip을 수행할 때
# duty_end >= max(arr_time of assigned trips) + 30
# duty_start <= min(dep_time of assigned trips) - 40
# duty_end - duty_start가 매우 커질 수 있음

# 5. model.json에서 duty_end의 upper_bound 확인
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)
for v in model.get('variables', []):
    print(f"\n  {v.get('id')}: lb={v.get('lower_bound')}, ub={v.get('upper_bound')}")

# 6. cleanup만 있고 preparation 없으면 왜 실패?
# duty_end[j] >= arr_time[i] + 30 - 1440*(1-x[i,j])
# trip_coverage: sum(x[i,j] for j) == 1 => 각 trip에 대해 어떤 j에서 x=1
# x=1인 (i,j)에 대해: duty_end[j] >= arr_time[i] + 30
# max_work_time도 있음: prep*y + sum(dur*x) + cleanup*y <= 660
# y[j]가 없으면? => y[j]는 목적함수에서 minimize sum(y[j])

# 핵심 확인: max_work_time에서 y[j]=0이면 prep*0 + sum(dur*x) + cleanup*0 <= 660
# 하지만 y[j]=0이어도 x[i,j]=1이 될 수 있나?
# => 모델에 x[i,j] <= y[j] 제약이 없음!

print(f'\n=== CRITICAL CHECK ===')
constraint_names = [c.get('name') for c in model.get('constraints', [])]
print(f'  Has "linking" constraint (x <= y)? {"linking" in str(constraint_names).lower()}')
# 전체 제약에서 x <= y 또는 x[i,j] <= y[j] 패턴 검색
with open('uploads/94/all_constraints_backup.json', encoding='utf-8') as f:
    all_c = json.load(f)
for c in all_c:
    expr = c.get('expression', '')
    if 'y[j]' in expr and 'x[' in expr and '<=' in expr:
        print(f'  Found linking: {c.get("name")}: {expr}')
