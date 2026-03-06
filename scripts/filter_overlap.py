import json
import csv

# overlap_pairs 필터링: arr_time[i1] < dep_time[i2]인 pair만 남기기
# (i1이 끝난 후 i2가 시작하는 실제 연속 가능한 pair)

with open('uploads/94/normalized/trips.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

trip_map = {}
for r in rows:
    trip_map[r['trip_id']] = {
        'dep': int(r['trip_dep_time']),
        'arr': int(r['trip_arr_time'])
    }

with open('uploads/94/normalized/overlap_pairs.json', encoding='utf-8') as f:
    all_pairs = json.load(f)

print(f'Original overlap_pairs: {len(all_pairs)}')

# 필터: i1의 도착 후 i2가 출발하는 pair만 (실제 연속 가능)
# 또한 gap이 max_single_wait_minutes(300분) 이내인 것만
filtered = []
for pair in all_pairs:
    i1, i2 = str(pair[0]), str(pair[1])
    if i1 in trip_map and i2 in trip_map:
        arr1 = trip_map[i1]['arr']
        dep2 = trip_map[i2]['dep']
        gap = dep2 - arr1
        if 0 < gap <= 300:  # 도착 후 출발, gap <= 300분
            filtered.append(pair)

print(f'Filtered overlap_pairs: {len(filtered)}')
print(f'Reduction: {len(all_pairs)} -> {len(filtered)} ({100*(1-len(filtered)/len(all_pairs)):.1f}% reduction)')
print(f'Expected constraints: {len(filtered)} * 96 = {len(filtered) * 96} (was {len(all_pairs) * 96})')

if filtered:
    print(f'Sample: {filtered[:5]}')

# 저장
with open('uploads/94/normalized/overlap_pairs_filtered.json', 'w', encoding='utf-8') as f:
    json.dump(filtered, f)

# 원본 백업 후 교체
import shutil
shutil.copy('uploads/94/normalized/overlap_pairs.json', 'uploads/94/normalized/overlap_pairs_original.json')
with open('uploads/94/normalized/overlap_pairs.json', 'w', encoding='utf-8') as f:
    json.dump(filtered, f)

print(f'\n[OK] overlap_pairs.json replaced with {len(filtered)} filtered pairs')
print('Original backed up to overlap_pairs_original.json')
