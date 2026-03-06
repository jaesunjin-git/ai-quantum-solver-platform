#!/usr/bin/env python3
"""
verify_split.py – 분리된 railway 도메인 파일 검증
"""
import os, yaml, sys

BASE = os.path.join("knowledge", "domains", "railway")
REQUIRED_FILES = ["_index.yaml", "constraints.yaml", "templates.yaml", "reference_ranges.yaml"]

errors = []
warnings = []

# ── 1. 파일 존재 확인 ─────────────────────────────────────
print("=" * 60)
print("1. 파일 존재 확인")
print("=" * 60)
for f in REQUIRED_FILES:
    path = os.path.join(BASE, f)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        print(f"  OK  {f}  ({size:,} bytes)")
    else:
        errors.append(f"MISSING: {path}")
        print(f"  FAIL  {f}  -- 파일 없음!")

# ── 2. YAML 파싱 확인 ─────────────────────────────────────
print("\n" + "=" * 60)
print("2. YAML 파싱 확인")
print("=" * 60)
data = {}
for f in REQUIRED_FILES:
    path = os.path.join(BASE, f)
    if not os.path.isfile(path):
        continue
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
            data[f] = d
            print(f"  OK  {f}  (top-level keys: {list(d.keys()) if isinstance(d, dict) else type(d).__name__})")
    except Exception as e:
        errors.append(f"PARSE ERROR in {f}: {e}")
        print(f"  FAIL  {f}  -- {e}")

# ── 3. constraints.yaml 상세 검증 ─────────────────────────
print("\n" + "=" * 60)
print("3. constraints.yaml 상세 검증")
print("=" * 60)
if "constraints.yaml" in data:
    c = data["constraints.yaml"]
    hard = c.get("hard", {})
    soft = c.get("soft", {})
    print(f"  Hard constraints: {len(hard)}개")
    print(f"  Soft constraints: {len(soft)}개")

    # 필수 신규 하드 제약 확인
    required_hard = [
        "max_single_wait_time", "night_sleep_guarantee",
        "day_duty_start", "day_duty_end", "night_duty_start",
        "day_night_classification", "meal_break_guarantee"
    ]
    for rh in required_hard:
        if rh in hard:
            hints = len(hard[rh].get("detection_hints", []))
            print(f"    OK  {rh}  (hints: {hints})")
        else:
            errors.append(f"MISSING hard constraint: {rh}")
            print(f"    FAIL  {rh}  -- 없음!")

    # 필수 신규 소프트 제약 확인
    required_soft = [
        "avg_driving_time_target", "avg_wait_time_target",
        "post_arrival_rest", "first_second_half_balance", "post_shift_training"
    ]
    for rs in required_soft:
        if rs in soft:
            w = soft[rs].get("weight", "N/A")
            print(f"    OK  {rs}  (weight: {w})")
        else:
            errors.append(f"MISSING soft constraint: {rs}")
            print(f"    FAIL  {rs}  -- 없음!")

    # detection_hints 전수 검사
    no_hints = []
    for name, spec in {**hard, **soft}.items():
        if name == "big_m_constant":
            continue
        hints = spec.get("detection_hints", [])
        if not hints:
            no_hints.append(name)
    if no_hints:
        warnings.append(f"detection_hints 비어있음: {no_hints}")
        print(f"\n  WARNING: detection_hints 없는 제약: {no_hints}")

# ── 4. templates.yaml 상세 검증 ───────────────────────────
print("\n" + "=" * 60)
print("4. templates.yaml 상세 검증")
print("=" * 60)
if "templates.yaml" in data:
    t = data["templates.yaml"]
    templates = t.get("constraint_templates", {})
    print(f"  Templates: {len(templates)}개")

    has_structured = 0
    for name, spec in templates.items():
        if "structured" in spec:
            has_structured += 1
    print(f"  Structured 포함: {has_structured}개")

    # 신규 템플릿 확인
    required_templates = [
        "max_single_wait_time",
        "day_night_classification_upper", "day_night_classification_lower",
        "day_duty_start_constraint", "day_duty_end_constraint",
        "night_duty_start_constraint", "night_sleep_guarantee",
        "meal_break_guarantee", "minimize_duties_with_soft"
    ]
    for rt in required_templates:
        if rt in templates:
            print(f"    OK  {rt}")
        else:
            errors.append(f"MISSING template: {rt}")
            print(f"    FAIL  {rt}  -- 없음!")

# ── 5. reference_ranges.yaml 검증 ─────────────────────────
print("\n" + "=" * 60)
print("5. reference_ranges.yaml 검증")
print("=" * 60)
if "reference_ranges.yaml" in data:
    r = data["reference_ranges.yaml"]
    new_params = [
        "max_single_wait_minutes", "min_night_sleep_minutes",
        "day_duty_start_earliest", "day_duty_end_latest",
        "night_duty_start_earliest", "night_threshold",
        "min_meal_break_minutes", "avg_driving_target_minutes",
        "avg_wait_target_minutes", "post_arrival_rest_minutes",
        "training_cycle_duties"
    ]
    for region_key, region in r.items():
        vals = region.get("values", {})
        print(f"  {region_key}: {len(vals)}개 파라미터")
        missing = [p for p in new_params if p not in vals]
        if missing:
            errors.append(f"{region_key} missing params: {missing}")
            print(f"    FAIL  누락: {missing}")
        else:
            print(f"    OK  신규 파라미터 {len(new_params)}개 모두 포함")

# ── 6. domain_loader 호환성 확인 ──────────────────────────
print("\n" + "=" * 60)
print("6. domain_loader 호환성 확인")
print("=" * 60)
old_file = os.path.join("knowledge", "domains", "railway.yaml")
if os.path.isfile(old_file):
    warnings.append("railway.yaml 단일파일이 아직 존재 (폴더보다 우선될 수 있음)")
    print(f"  WARNING: {old_file} 아직 존재! 폴더와 충돌 가능")
else:
    print(f"  OK  {old_file} 제거됨 (폴더 구조만 존재)")

# ── 결과 요약 ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("검증 결과 요약")
print("=" * 60)
if errors:
    print(f"\n  ERRORS ({len(errors)}):")
    for e in errors:
        print(f"    - {e}")
    sys.exit(1)
else:
    print(f"\n  ALL PASSED!")
    if warnings:
        print(f"\n  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")
    print(f"\n  Hard: {len(data.get('constraints.yaml', {}).get('hard', {}))}개")
    print(f"  Soft: {len(data.get('constraints.yaml', {}).get('soft', {}))}개")
    print(f"  Templates: {len(data.get('templates.yaml', {}).get('constraint_templates', {}))}개")
    print(f"  Reference regions: {len(data.get('reference_ranges.yaml', {}))}개")
    print(f"\n  다음 단계: Step 2 (sequential_pairs.json 생성) 진행 가능")
    sys.exit(0)