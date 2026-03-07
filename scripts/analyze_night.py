import json

with open('uploads/94/all_constraints_backup.json', encoding='utf-8') as f:
    all_constraints = json.load(f)

# night_duty_start 상세 분석
for c in all_constraints:
    if c.get('name') == 'night_duty_start':
        print(f"expression: {c.get('expression')}")
        print(f"for_each: {c.get('for_each')}")

# 제약: duty_start[j] >= night_duty_start_earliest - big_m * (1 - is_night[j])
# is_night[j]=1 (야간): duty_start[j] >= 1080
# is_night[j]=0 (주간): duty_start[j] >= 1080 - 1440 = -360 (무조건 만족)

# day_duty_start: duty_start[j] >= day_duty_start_earliest * (1 - is_night[j])
# is_night[j]=0 (주간): duty_start[j] >= 380
# is_night[j]=1 (야간): duty_start[j] >= 0 (무조건 만족)

# day_duty_end: duty_end[j] <= day_duty_end_latest + big_m * is_night[j]
# is_night[j]=0 (주간): duty_end[j] <= 1380
# is_night[j]=1 (야간): duty_end[j] <= 1380 + 1440 = 2820 (무조건 만족)

print('\n=== Constraint analysis ===')
print('night_duty_start: duty_start[j] >= night_duty_start_earliest - big_m * (1 - is_night[j])')
print('  is_night=1: duty_start[j] >= 1080 (18:00)')
print('  is_night=0: duty_start[j] >= 1080 - 1440 = -360 (always OK)')

print('\nday_duty_start: duty_start[j] >= day_duty_start_earliest * (1 - is_night[j])')
print('  is_night=0: duty_start[j] >= 380 (06:20)')
print('  is_night=1: duty_start[j] >= 0 (always OK)')

print('\nday_duty_end: duty_end[j] <= day_duty_end_latest + big_m * is_night[j]')
print('  is_night=0: duty_end[j] <= 1380 (23:00)')
print('  is_night=1: duty_end[j] <= 2820 (always OK)')

# 문제: 야간 근무자(is_night=1)가 있어야 하는데...
# trip_coverage: 모든 trip이 배정되어야 함
# dep_time 범위: 316~1439
# 316(05:16)에 출발하는 trip -> 준비시간 40분 -> duty_start <= 276
# 하지만 is_night=1이면 duty_start >= 1080 -> 모순!
# is_night=0이면 day_duty_start: duty_start >= 380 -> 276 < 380 -> 모순!

import csv
with open('uploads/94/normalized/trips.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

dep_times = sorted([int(r['trip_dep_time']) for r in rows])

print(f'\n=== Earliest trips ===')
for r in sorted(rows, key=lambda x: int(x['trip_dep_time']))[:10]:
    dep = int(r['trip_dep_time'])
    required_start = dep - 40  # preparation
    print(f"  trip {r['trip_id']}: dep={dep} ({dep//60:02d}:{dep%60:02d}), required duty_start <= {required_start}")
    print(f"    is_night=0: day_duty_start requires >= 380 -> {'OK' if required_start >= 380 else 'CONFLICT! ' + str(required_start) + ' < 380'}")
    print(f"    is_night=1: night_duty_start requires >= 1080 -> {'OK' if required_start >= 1080 else 'CONFLICT!'}")
