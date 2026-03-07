import json
import csv

with open('uploads/94/normalized/trips.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

trip_map = {}
for r in rows:
    trip_map[r['trip_id']] = {
        'dep': int(r['trip_dep_time']),
        'arr': int(r['trip_arr_time'])
    }

with open('uploads/94/normalized/overlap_pairs_original.json', encoding='utf-8') as f:
    all_pairs = json.load(f)

# 실제 gap 분포 확인
gaps = []
samples = []
for pair in all_pairs[:50]:
    i1, i2 = str(pair[0]), str(pair[1])
    if i1 in trip_map and i2 in trip_map:
        arr1 = trip_map[i1]['arr']
        dep2 = trip_map[i2]['dep']
        gap = dep2 - arr1
        gaps.append(gap)
        if len(samples) < 20:
            samples.append({
                'i1': i1, 'i2': i2,
                'dep1': trip_map[i1]['dep'], 'arr1': arr1,
                'dep2': dep2, 'arr2': trip_map[i2]['arr'],
                'gap': gap
            })

print('=== First 20 pair gaps ===')
for s in samples:
    print(f"  ({s['i1']},{s['i2']}): dep1={s['dep1']} arr1={s['arr1']} | dep2={s['dep2']} arr2={s['arr2']} | gap={s['gap']}")

if gaps:
    print(f'\n  Min gap: {min(gaps)}')
    print(f'  Max gap: {max(gaps)}')
    print(f'  Positive gaps (arr1 < dep2): {sum(1 for g in gaps if g > 0)}')
    print(f'  Negative gaps (arr1 >= dep2): {sum(1 for g in gaps if g <= 0)}')

# 전체 pair gap 분포
all_gaps = []
for pair in all_pairs:
    i1, i2 = str(pair[0]), str(pair[1])
    if i1 in trip_map and i2 in trip_map:
        gap = trip_map[i2]['dep'] - trip_map[i1]['arr']
        all_gaps.append(gap)

print(f'\n=== All pair gap distribution ===')
print(f'  Total pairs: {len(all_gaps)}')
print(f'  Min: {min(all_gaps)}, Max: {max(all_gaps)}')
print(f'  gap > 0: {sum(1 for g in all_gaps if g > 0)}')
print(f'  gap > 0 and <= 300: {sum(1 for g in all_gaps if 0 < g <= 300)}')
print(f'  gap <= 0: {sum(1 for g in all_gaps if g <= 0)}')
