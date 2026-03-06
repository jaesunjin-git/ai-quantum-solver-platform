import sys; sys.path.insert(0, '.')

TARGET = "knowledge/domains/railway/constraints.yaml"

with open(TARGET, "r", encoding="utf-8") as f:
    raw = f.read()

with open(TARGET + ".bak4", "w", encoding="utf-8") as f:
    f.write(raw)
print(f"[backup] {TARGET}")

changes = 0

# 1. night_rest: "입고", "출고", "확보 필요", "익일" 추가
old = '''  night_rest:
    name_ko: "야간 휴식"
    description: "숙박을 포함하는 근무 시 최소 야간 수면시간"
    type: lower_bound
    parameter: min_night_rest_minutes
    unit: minutes
    typical_range: [240, 480]
    detection_hints:
      - "야간 휴식"
      - "수면"
      - "night rest"
      - "숙박"
      - "숙면"'''

new = '''  night_rest:
    name_ko: "야간 휴식"
    description: "숙박을 포함하는 근무 시 최소 야간 수면시간"
    type: lower_bound
    parameter: min_night_rest_minutes
    unit: minutes
    typical_range: [240, 480]
    context_must: ["야간", "숙박", "입고", "익일", "확보 필요", "취침"]
    context_exclude: ["출고시간", "주간", "첫 출고"]
    detection_hints:
      - "야간 휴식"
      - "수면"
      - "night rest"
      - "숙박"
      - "숙면"
      - "당일입고"
      - "익일출고"
      - "확보 필요"'''

if old in raw:
    raw = raw.replace(old, new)
    changes += 1
    print("[1] night_rest: OK")
else:
    print("[1] night_rest: FAILED")

# 2. day_duty_start: context_exclude에 "입고", "익일" 추가
old2 = '''    context_must: ["주간", day, "시작", start, "출고", "이후"]
    context_exclude: ["야간", night, "정리", "점호"]'''

new2 = '''    context_must: ["주간", day, "시작", start, "출고", "이후"]
    context_exclude: ["야간", night, "정리", "점호", "입고", "익일", "당일입고", "확보 필요"]'''

if old2 in raw:
    raw = raw.replace(old2, new2)
    changes += 1
    print("[2] day_duty_start exclude: OK")
else:
    print("[2] day_duty_start exclude: FAILED")

# 3. post_arrival_rest: context_exclude에 "전반", "후반", "구분" 추가
old3 = '''    context_must: ["퇴근", "후", post, rest, "강차", "휴양"]'''

new3 = '''    context_must: ["퇴근", "후", post, rest, "강차", "휴양"]
    context_exclude: ["전반", "후반", "구분", "전반사업", "후반사업"]'''

if old3 in raw:
    raw = raw.replace(old3, new3)
    changes += 1
    print("[3] post_arrival_rest exclude: OK")
else:
    print("[3] post_arrival_rest exclude: FAILED")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(raw)

print(f"\n✅ {changes}개 패치 적용")

# 검증
from domains.crew.skills.structural_normalization import ConstraintSemanticMapper
m = ConstraintSemanticMapper()
tests = [
    ('duration', '당일입고:30분, 익일출고:50분 포함 시 5시간 20분 확보 필요', 320, 'minutes', 'min_night_rest_minutes'),
    ('duration', '야간사업 취침시간 4시간 확보', 240, 'minutes', 'min_night_sleep_minutes'),
    ('duration', '주간사업 식사와 휴양을 고려 전반사업과 후반사업으로 구분', 0, 'minutes', 'NOT post_arrival'),
    ('time_of_day', '주간사업의 첫 출고는 06:20 이후', 380, 'minutes', 'day_duty_start_earliest'),
]
print("\n[검증]")
all_ok = True
for n, c, v, u, expected in tests:
    r = m.map_param(n, c, v, u)
    ok = "OK" if expected in r or (expected.startswith("NOT") and expected.split(" ")[1] not in r) else "FAIL"
    if ok == "FAIL":
        all_ok = False
    print(f"  {ok}  {c[:45]:47s} -> {r} (expected: {expected})")

if all_ok:
    print("\n모든 매핑 정확!")
else:
    print("\n일부 매핑 실패 - 추가 조정 필요")
