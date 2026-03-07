import yaml

# ============================================================
# 1. constraints.yaml에 objective 섹션 확장 + 연동 규칙 추가
# ============================================================
with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

# objective 섹션 확장
data['objectives'] = {
    'minimize_duties': {
        'type': 'minimize',
        'expression': 'sum(y[j] for j in J)',
        'description': '총 승무원(근무) 수 최소화',
        'description_ko': '승무원 수 최소화',
        'required_constraints': ['trip_coverage', 'crew_activation_linking', 'no_overlap'],
        'recommended_hard': ['max_driving_time', 'max_work_time'],
        'recommended_soft': ['workload_balance', 'avg_driving_time_target'],
        'is_default': True,
    },
    'minimize_duties_with_penalties': {
        'type': 'minimize',
        'expression': 'sum(y[j] for j in J) + sum(weight_k * slack_k for k in soft_constraints)',
        'description': '승무원 수 최소화 + soft 제약 위반 페널티',
        'description_ko': '승무원 수 최소화 (soft 페널티 포함)',
        'required_constraints': ['trip_coverage', 'crew_activation_linking', 'no_overlap'],
        'recommended_hard': ['max_driving_time', 'max_work_time'],
        'recommended_soft': ['workload_balance', 'avg_driving_time_target', 'avg_wait_time_target'],
        'requires_soft': True,
    },
    'maximize_efficiency': {
        'type': 'maximize',
        'expression': 'sum(trip_duration[i] * x[i,j] for i in I for j in J) / sum(duty_end[j] - duty_start[j] for j in J)',
        'description': '운전 효율(driving efficiency) 최대화',
        'description_ko': '운전 효율 최대화',
        'required_constraints': ['trip_coverage', 'crew_activation_linking', 'no_overlap'],
        'recommended_hard': ['max_driving_time', 'max_work_time', 'max_wait_time'],
        'recommended_soft': ['avg_wait_time_target'],
        'promote_to_hard': ['max_total_stay_time'],
    },
    'balance_workload': {
        'type': 'minimize',
        'expression': 'max(trips_per_crew) - min(trips_per_crew)',
        'description': '승무원 간 업무량 균형화',
        'description_ko': '업무량 균형 최적화',
        'required_constraints': ['trip_coverage', 'crew_activation_linking', 'no_overlap'],
        'recommended_hard': ['max_driving_time', 'max_work_time'],
        'promote_to_hard': ['workload_balance'],
        'recommended_soft': ['avg_driving_time_target'],
    },
}

# 기존 단순 objective 제거
if 'objective' in data:
    del data['objective']

with open('knowledge/domains/railway/constraints.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)
print('1. constraints.yaml objectives updated')

# ============================================================
# 2. _index.yaml에 problem_variants + typical_objectives 추가
# ============================================================
with open('knowledge/domains/railway/_index.yaml', encoding='utf-8') as f:
    idx = yaml.safe_load(f)

idx['problem_variants'] = {
    'duty_generation': {
        'name_ko': '사업(근무) 생성',
        'description': '트립을 승무원 근무에 배정하여 최소 근무 수를 결정',
        'typical_objectives': ['minimize_duties', 'minimize_duties_with_penalties', 'maximize_efficiency'],
    },
    'roster_assignment': {
        'name_ko': '교번표 배정',
        'description': '생성된 근무를 승무원에게 배정',
        'typical_objectives': ['balance_workload'],
    },
}

# stages 추가 (없으면)
if 'stages' not in idx:
    idx['stages'] = {}
idx['stages']['task_generation'] = {
    'name_ko': '사업 생성',
    'typical_objectives': ['minimize_duties', 'minimize_duties_with_penalties', 'maximize_efficiency', 'balance_workload'],
}

with open('knowledge/domains/railway/_index.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(idx, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)
print('2. _index.yaml updated with objectives')

# ============================================================
# 3. 검증
# ============================================================
with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    verify = yaml.safe_load(f)

print(f'\nObjectives: {len(verify.get("objectives", {}))}')
for name, obj in verify.get('objectives', {}).items():
    req = obj.get('required_constraints', [])
    promote = obj.get('promote_to_hard', [])
    print(f'  {name}:')
    print(f'    type={obj["type"]}, required={len(req)}, promote_to_hard={promote}')

with open('knowledge/domains/railway/_index.yaml', encoding='utf-8') as f:
    verify_idx = yaml.safe_load(f)
stages = verify_idx.get('stages', {})
for sname, sdata in stages.items():
    print(f'\nStage: {sname}')
    print(f'  typical_objectives: {sdata.get("typical_objectives", [])}')
