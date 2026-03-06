import yaml

with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    d = yaml.safe_load(f)

templates = {
    'trip_coverage': {
        'expression_template': 'sum(x[i,j] for j in J) == 1',
        'for_each': 'i in I',
        'description_math': '각 운행 i는 정확히 하나의 duty j에 배정',
        'variables_used': ['x[I,J]'],
    },
    'max_driving_time': {
        'expression_template': 'sum(trip_duration[i] * x[i,j] for i in I) <= max_driving_minutes',
        'for_each': 'j in J',
        'description_math': 'duty j에 배정된 운행들의 운전시간 합 <= 300분',
        'variables_used': ['x[I,J]'],
    },
    'max_work_time': {
        'expression_template': 'preparation_minutes * y[j] + sum(trip_duration[i] * x[i,j] for i in I) + cleanup_minutes * y[j] <= max_work_minutes',
        'for_each': 'j in J',
        'description_math': 'duty j의 총 구속시간(준비+운전+정리) <= 660분',
        'variables_used': ['x[I,J]', 'y[J]'],
    },
    'mandatory_break': {
        'expression_template': 'duty_end[j] - duty_start[j] - sum(trip_duration[i] * x[i,j] for i in I) >= min_break_minutes * y[j]',
        'for_each': 'j in J',
        'description_math': 'duty span - 운전시간 >= 최소 휴식시간',
        'variables_used': ['x[I,J]', 'y[J]', 'duty_start[J]', 'duty_end[J]'],
    },
    'preparation_time': {
        'expression_template': 'duty_start[j] <= trip_dep_time[i] - preparation_minutes + big_m * (1 - x[i,j])',
        'for_each': 'i in I, j in J',
        'description_math': 'duty 시작은 배정된 모든 trip 출발 - 준비시간 이전',
        'variables_used': ['x[I,J]', 'duty_start[J]'],
    },
    'cleanup_time': {
        'expression_template': 'duty_end[j] >= trip_arr_time[i] + cleanup_minutes - big_m * (1 - x[i,j])',
        'for_each': 'i in I, j in J',
        'description_math': 'duty 종료는 배정된 모든 trip 도착 + 정리시간 이후',
        'variables_used': ['x[I,J]', 'duty_end[J]'],
    },
    'night_rest': {
        'expression_template': 'duty_end[j] - duty_start[j] <= 1440 - min_night_rest_minutes',
        'for_each': 'j in J',
        'description_math': 'duty span <= 1440 - 야간휴식시간 (하루 내 근무 제한)',
        'variables_used': ['duty_start[J]', 'duty_end[J]'],
    },
    'max_total_stay_time': {
        'expression_template': 'duty_end[j] - duty_start[j] <= max_total_stay_minutes',
        'for_each': 'j in J',
        'description_math': 'duty 총 체재시간 <= 720분',
        'variables_used': ['duty_start[J]', 'duty_end[J]'],
    },
    'big_m_constant': {
        'expression_template': 'CONSTANT: M = big_m (제약 아님, 다른 제약에서 참조)',
        'for_each': '',
        'description_math': 'Big-M 상수 정의',
        'variables_used': [],
    },
    'qualification': {
        'expression_template': 'SKIP: 자격 데이터 없으면 생략 (모두 자격 있다고 가정)',
        'for_each': '',
        'description_math': '자격 제약 (데이터 없으면 생략)',
        'variables_used': [],
    },
    'max_wait_time': {
        'expression_template': 'duty_end[j] - duty_start[j] - sum(trip_duration[i] * x[i,j] for i in I) - preparation_minutes * y[j] - cleanup_minutes * y[j] <= max_wait_minutes',
        'for_each': 'j in J',
        'description_math': 'duty 내 총 대기시간(span - 운전 - 준비 - 정리) <= 300분',
        'variables_used': ['x[I,J]', 'y[J]', 'duty_start[J]', 'duty_end[J]'],
    },
    'min_wait_time': {
        'expression_template': 'trip_dep_time[i2] - trip_arr_time[i1] >= min_wait_minutes - big_m * (2 - x[i1,j] - x[i2,j])',
        'for_each': '(i1,i2) in overlap_pairs, j in J',
        'description_math': '같은 duty 내 연속 운행 간 최소 대기 >= 10분',
        'variables_used': ['x[I,J]'],
    },
    'max_single_wait_time': {
        'expression_template': 'trip_dep_time[i2] - trip_arr_time[i1] <= max_single_wait_minutes + big_m * (2 - x[i1,j] - x[i2,j])',
        'for_each': '(i1,i2) in overlap_pairs, j in J',
        'description_math': '같은 duty 내 연속 운행 간 1회 대기 <= 300분',
        'variables_used': ['x[I,J]'],
    },
    'night_sleep_guarantee': {
        'expression_template': '1440 - duty_end[j] + duty_start[j] >= min_night_sleep_minutes * is_night[j]',
        'for_each': 'j in J',
        'description_math': '야간 duty 수면시간 >= 240분',
        'variables_used': ['duty_start[J]', 'duty_end[J]', 'is_night[J]'],
    },
    'day_duty_start': {
        'expression_template': 'duty_start[j] >= day_duty_start_earliest * (1 - is_night[j])',
        'for_each': 'j in J',
        'description_math': '주간 duty 시작 >= 06:20',
        'variables_used': ['duty_start[J]', 'is_night[J]'],
    },
    'day_duty_end': {
        'expression_template': 'duty_end[j] <= day_duty_end_latest + big_m * is_night[j]',
        'for_each': 'j in J',
        'description_math': '주간 duty 종료 <= 23:00',
        'variables_used': ['duty_end[J]', 'is_night[J]'],
    },
    'night_duty_start': {
        'expression_template': 'duty_start[j] >= night_duty_start_earliest - big_m * (1 - is_night[j])',
        'for_each': 'j in J',
        'description_math': '야간 duty 시작 >= 18:00',
        'variables_used': ['duty_start[J]', 'is_night[J]'],
    },
    'day_night_classification': {
        'expression_template': 'duty_start[j] >= night_threshold - big_m * (1 - is_night[j])',
        'for_each': 'j in J',
        'description_math': 'duty 시작 >= 22:00이면 야간 분류',
        'variables_used': ['duty_start[J]', 'is_night[J]'],
    },
    'meal_break_guarantee': {
        'expression_template': 'duty_end[j] - duty_start[j] - sum(trip_duration[i] * x[i,j] for i in I) >= min_meal_break_minutes * y[j]',
        'for_each': 'j in J',
        'description_math': 'duty 내 비운전 시간 >= 식사 최소 휴식',
        'variables_used': ['x[I,J]', 'y[J]', 'duty_start[J]', 'duty_end[J]'],
    },
}

# constraints.yaml에 병합
for cname, tmpl in templates.items():
    if cname in d['hard']:
        d['hard'][cname].update(tmpl)

# 보조변수
d['auxiliary_variables'] = {
    'duty_start': {'type': 'continuous', 'indices': ['J'], 'description': 'duty j 시작시각(분)'},
    'duty_end': {'type': 'continuous', 'indices': ['J'], 'description': 'duty j 종료시각(분)'},
    'is_night': {'type': 'binary', 'indices': ['J'], 'description': 'duty j 야간 여부'},
}

# constraint_templates 키도 추가 (프롬프트용)
d['constraint_templates'] = {}
for cname, tmpl in templates.items():
    d['constraint_templates'][cname] = {
        'expression': tmpl['expression_template'],
        'for_each': tmpl['for_each'],
        'description': tmpl['description_math'],
    }

# domain 키 추가 (_load_domain_yaml 호환)
d['domain'] = 'railway'

with open('knowledge/domains/railway/constraints.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(d, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

with_tmpl = sum(1 for v in d['hard'].values() if 'expression_template' in v)
print(f'Hard constraints: {len(d["hard"])} (with template: {with_tmpl})')
print(f'Auxiliary variables: {len(d["auxiliary_variables"])}')
print(f'Constraint templates: {len(d["constraint_templates"])}')
print(f'Domain key: {d.get("domain")}')
