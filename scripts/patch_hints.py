#!/usr/bin/env python3
"""
scripts/patch_hints.py
constraints.yaml의 detection_hints 보강 - 사용자 데이터 미매핑 해소
"""
import yaml

TARGET = "knowledge/domains/railway/constraints.yaml"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()
    f.seek(0)
    data = yaml.safe_load(f)

with open(TARGET + ".bak3", "w", encoding="utf-8") as f:
    f.write(content)
print(f"[backup] {TARGET}")

changes = 0

# ── 1. night_duty_start: "출고시간", "출고" 추가 ──
h = data["hard"]["night_duty_start"]
hints = h.get("detection_hints", [])
for new_hint in ["출고시간", "출고", "야간사업 출고"]:
    if new_hint not in hints:
        hints.append(new_hint)
        changes += 1
ctx_must = h.get("context_must", [])
for new_kw in ["출고", "이후"]:
    if new_kw not in ctx_must:
        ctx_must.append(new_kw)
h["context_must"] = ctx_must
print(f"[1] night_duty_start: hints={len(hints)}, ctx_must={len(ctx_must)}")

# ── 2. night_sleep_guarantee: "취침시간", "취침", "확보" 추가 ──
h2 = data["hard"]["night_sleep_guarantee"]
hints2 = h2.get("detection_hints", [])
for new_hint in ["취침시간", "취침", "야간사업 취침"]:
    if new_hint not in hints2:
        hints2.append(new_hint)
        changes += 1
ctx_must2 = h2.get("context_must", [])
for new_kw in ["취침", "확보"]:
    if new_kw not in ctx_must2:
        ctx_must2.append(new_kw)
h2["context_must"] = ctx_must2
print(f"[2] night_sleep_guarantee: hints={len(hints2)}, ctx_must={len(ctx_must2)}")

# ── 3. max_total_stay_time: "체류시간", "체류" 추가 ──
h3 = data["hard"]["max_total_stay_time"]
hints3 = h3.get("detection_hints", [])
for new_hint in ["체류시간", "체류", "회사 내 체류"]:
    if new_hint not in hints3:
        hints3.append(new_hint)
        changes += 1
ctx_must3 = h3.get("context_must", [])
for new_kw in ["이내", "이하", "체류", "가급적"]:
    if new_kw not in ctx_must3:
        ctx_must3.append(new_kw)
h3["context_must"] = ctx_must3
print(f"[3] max_total_stay_time: hints={len(hints3)}, ctx_must={len(ctx_must3)}")

# ── 4. avg_wait_time_target (soft): "인정대기", "실대기", "인정 대기" 추가 ──
s1 = data["soft"]["avg_wait_time_target"]
hints4 = s1.get("detection_hints", [])
for new_hint in ["인정대기", "실대기", "인정 대기", "인정대기시간"]:
    if new_hint not in hints4:
        hints4.append(new_hint)
        changes += 1
print(f"[4] avg_wait_time_target: hints={len(hints4)}")

# ── 5. post_arrival_rest (soft): "강차", "휴양시간", "휴양" 추가 ──
s2 = data["soft"]["post_arrival_rest"]
hints5 = s2.get("detection_hints", [])
for new_hint in ["강차", "휴양시간", "휴양", "강차 후"]:
    if new_hint not in hints5:
        hints5.append(new_hint)
        changes += 1
ctx_must5 = s2.get("context_must", [])
for new_kw in ["강차", "휴양"]:
    if new_kw not in ctx_must5:
        ctx_must5.append(new_kw)
s2["context_must"] = ctx_must5
print(f"[5] post_arrival_rest: hints={len(hints5)}, ctx_must={len(ctx_must5)}")

# ── 6. mandatory_break: "인정 대기시간" (테이블에서 온 것) ──
h6 = data["hard"]["mandatory_break"]
hints6 = h6.get("detection_hints", [])
for new_hint in ["인정 대기시간", "인정대기시간"]:
    if new_hint not in hints6:
        hints6.append(new_hint)
        changes += 1
print(f"[6] mandatory_break: hints={len(hints6)}")

# ── 저장 (원본 포맷 유지를 위해 문자열 치환 방식) ──
# yaml.dump는 한글이 이스케이프되므로 직접 치환

with open(TARGET, "r", encoding="utf-8") as f:
    raw = f.read()

# night_duty_start hints 교체
old_nds_hints = '''    detection_hints:
      - "야간 시작"
      - "야간근무 시작"
      - "night duty start"
      - "야근 시작"'''
new_nds_hints = '''    detection_hints:
      - "야간 시작"
      - "야간근무 시작"
      - "night duty start"
      - "야근 시작"
      - "출고시간"
      - "출고"
      - "야간사업 출고"'''

old_nds_ctx = '''    context_must: ["야간", night, "시작", start]'''
new_nds_ctx = '''    context_must: ["야간", night, "시작", start, "출고", "이후"]'''

# night_sleep_guarantee hints 교체
old_nsg_hints = '''    detection_hints:
      - "수면 보장"
      - "야간 수면"
      - "night sleep"
      - "숙면 보장"
      - "연속 수면"'''
new_nsg_hints = '''    detection_hints:
      - "수면 보장"
      - "야간 수면"
      - "night sleep"
      - "숙면 보장"
      - "연속 수면"
      - "취침시간"
      - "취침"
      - "야간사업 취침"'''

old_nsg_ctx = '''    context_must: ["보장", "최소", guarantee, min]'''
new_nsg_ctx = '''    context_must: ["보장", "최소", guarantee, min, "취침", "확보"]'''

# max_total_stay_time hints 교체
old_mts_hints = '''    detection_hints:
      - "체재시간"
      - "총 체재"
      - "total stay"
      - "구속시간"'''
new_mts_hints = '''    detection_hints:
      - "체재시간"
      - "총 체재"
      - "total stay"
      - "구속시간"
      - "체류시간"
      - "체류"
      - "회사 내 체류"'''

# max_total_stay_time에 context_must 추가 (기존에 없음)
old_mts_block = '''  max_total_stay_time:
    name_ko: "최대 총 체재시간"
    description: "하나의 근무 내 총 체재(대기 포함) 시간 상한"
    type: upper_bound
    parameter: max_total_stay_minutes
    unit: minutes
    typical_range: [600, 720]
    detection_hints:
      - "체재시간"
      - "총 체재"
      - "total stay"
      - "구속시간"
      - "체류시간"
      - "체류"
      - "회사 내 체류"'''
new_mts_block = '''  max_total_stay_time:
    name_ko: "최대 총 체재시간"
    description: "하나의 근무 내 총 체재(대기 포함) 시간 상한"
    type: upper_bound
    parameter: max_total_stay_minutes
    unit: minutes
    typical_range: [600, 720]
    context_must: ["이내", "이하", "체류", "가급적", "체재"]
    detection_hints:
      - "체재시간"
      - "총 체재"
      - "total stay"
      - "구속시간"
      - "체류시간"
      - "체류"
      - "회사 내 체류"'''

# avg_wait_time_target hints 교체
old_awt_hints = '''    detection_hints:
      - "평균 대기"
      - "avg wait"
      - "대기시간 평균"'''
new_awt_hints = '''    detection_hints:
      - "평균 대기"
      - "avg wait"
      - "대기시간 평균"
      - "인정대기"
      - "실대기"
      - "인정 대기"
      - "인정대기시간"'''

# post_arrival_rest hints 교체
old_par_hints = '''    detection_hints:
      - "퇴근 후 휴식"
      - "post shift rest"
      - "퇴근후 쉬는시간"
      - "근무간 휴식"'''
new_par_hints = '''    detection_hints:
      - "퇴근 후 휴식"
      - "post shift rest"
      - "퇴근후 쉬는시간"
      - "근무간 휴식"
      - "강차"
      - "휴양시간"
      - "휴양"
      - "강차 후"'''

old_par_ctx = '''    context_must: ["퇴근", "후", post, rest]'''
new_par_ctx = '''    context_must: ["퇴근", "후", post, rest, "강차", "휴양"]'''

# mandatory_break hints 교체
old_mb_hints = '''    detection_hints:
      - "휴식"
      - break
      - "식사시간"
      - "쉬는 시간"
      - rest'''
new_mb_hints = '''    detection_hints:
      - "휴식"
      - break
      - "식사시간"
      - "쉬는 시간"
      - rest
      - "인정 대기시간"
      - "인정대기시간"'''

# 순서대로 치환
replacements = [
    (old_nds_hints, new_nds_hints),
    (old_nds_ctx, new_nds_ctx),
    (old_nsg_hints, new_nsg_hints),
    (old_nsg_ctx, new_nsg_ctx),
    (old_mts_hints, new_mts_hints),
    (old_mts_block, new_mts_block),
    (old_awt_hints, new_awt_hints),
    (old_par_hints, new_par_hints),
    (old_par_ctx, new_par_ctx),
    (old_mb_hints, new_mb_hints),
]

applied = 0
for old, new in replacements:
    if old in raw:
        raw = raw.replace(old, new)
        applied += 1

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(raw)

print(f"\n✅ {applied}개 치환 적용 (총 {changes}개 힌트 추가)")

# 검증
data2 = yaml.safe_load(open(TARGET, encoding="utf-8"))
h_count = len(data2.get("hard", {}))
s_count = len(data2.get("soft", {}))
print(f"   검증: Hard={h_count}, Soft={s_count}")