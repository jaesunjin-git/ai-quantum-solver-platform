import yaml, shutil, os

# Backup current file
src = 'knowledge/domains/railway/constraints.yaml'
bak = 'knowledge/domains/railway/constraints_v2_backup.yaml'
if not os.path.exists(bak):
    shutil.copy2(src, bak)
    print(f'Backup saved: {bak}')

constraints_v3 = {
    'version': '3.0',
    'domain': 'railway',
    'description': '철도 승무원 스케줄링 제약조건 (범용 설계, 자동 분류 + 사용자 확인)',

    'category_rules': {
        'hard_keywords': ['이내', '이상', '이후', '이전', '확보', '반드시', '불가', '필수', 'must', 'shall', 'within', 'ensure'],
        'soft_keywords': ['가급적', '노력', '권장', '최대한', '되도록', '바람직', 'prefer', 'target', 'recommend', 'aim'],
        'force_hard_keywords': ['최소', '절대', '반드시', '필수', 'must', 'mandatory'],
        'resolution_rules': [
            'soft_keyword가 있으면 기본 SOFT',
            'force_hard_keyword가 있으면 해당 값은 HARD',
            'BOTH인 경우: _min 접미사 → HARD, _avg/_target → SOFT',
            '확보+노력 조합 → SOFT',
            'ambiguous → default_category 사용, 사용자 확인 요청'
        ],
        'semantic_id_patterns': {
            'hard': ['_min$', '_max_hard$', '_earliest_min$', '_latest_min$'],
            'soft': ['_avg$', '_target$', '_preferred$', '_recommended$']
        },
        'default_if_ambiguous': 'hard'
    },

    'variables': {
        'x': {
            'type': 'binary', 'indices': ['I', 'J'],
            'description': '트립 i를 crew j에 배정',
            'aliases': ['x[i,j]', 'x[i,d]']
        },
        'y': {
            'type': 'binary', 'indices': ['J'],
            'description': 'crew j 활성화 여부',
            'aliases': ['y[j]', 'u[d]']
        },
        'duty_start': {
            'type': 'integer', 'indices': ['J'],
            'description': 'crew j의 근무 시작 시각 (분)',
            'aliases': ['duty_start[j]', 's[d]']
        },
        'duty_end': {
            'type': 'integer', 'indices': ['J'],
            'description': 'crew j의 근무 종료 시각 (분)',
            'aliases': ['duty_end[j]', 'e[d]']
        },
        'is_night': {
            'type': 'binary', 'indices': ['J'],
            'description': 'crew j의 야간 근무 여부',
            'aliases': ['is_night[j]', 'n[d]']
        }
    },

    'sets': {
        'I': {'description': '트립 집합', 'source': 'trips.csv', 'column': 'trip_id'},
        'J': {'description': '근무/crew 집합', 'source': 'range', 'default_size': 96},
        'overlap_pairs': {'description': '시간 겹침 트립 쌍', 'source': 'overlap_pairs.json'}
    },

    'constraints': {
        # === 구조적 제약 (항상 hard) ===
        'trip_coverage': {
            'description': '모든 트립은 정확히 하나의 crew에 배정',
            'expression_template': 'sum(x[i,j] for j in J) == 1',
            'for_each': 'i in I',
            'parameters': [],
            'default_category': 'hard',
            'fixed_category': True,
            'structured': {
                'lhs': {'type': 'sum', 'variable': 'x', 'sum_over': 'j', 'indices': ['i', 'j']},
                'operator': '==',
                'rhs': {'type': 'constant', 'value': 1}
            }
        },
        'crew_activation_linking': {
            'description': 'crew가 하나라도 트립을 담당하면 활성화',
            'expression_template': 'y[j] >= x[i,j]',
            'for_each': 'i in I, j in J',
            'parameters': [],
            'default_category': 'hard',
            'fixed_category': True,
            'structured': {
                'lhs': {'type': 'variable', 'id': 'y', 'indices': ['j']},
                'operator': '>=',
                'rhs': {'type': 'variable', 'id': 'x', 'indices': ['i', 'j']}
            }
        },
        'no_overlap': {
            'description': '시간 겹치는 트립은 같은 crew에 배정 금지',
            'expression_template': 'x[i1,j] + x[i2,j] <= 1',
            'for_each': '(i1,i2) in overlap_pairs, j in J',
            'parameters': [],
            'default_category': 'hard',
            'fixed_category': True,
            'structured': {
                'lhs': {'type': 'sum', 'terms': [
                    {'type': 'variable', 'id': 'x', 'indices': ['i1', 'j']},
                    {'type': 'variable', 'id': 'x', 'indices': ['i2', 'j']}
                ]},
                'operator': '<=',
                'rhs': {'type': 'constant', 'value': 1}
            }
        },

        # === 운전/근무시간 제약 ===
        'max_driving_time': {
            'description': '1사업 최대 운전시간',
            'expression_template': 'sum(trip_duration[i] * x[i,j] for i in I) <= max_driving_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['max_driving_minutes'],
            'default_category': 'hard',
            'fixed_category': False,
            'context_param': 'avg_driving_target_minutes',
            'structured': {
                'lhs': {'type': 'sum', 'variable': 'x', 'sum_over': 'i', 'indices': ['i', 'j'], 'coefficient': 'trip_duration[i]'},
                'operator': '<=',
                'rhs': {'type': 'product', 'terms': [{'type': 'parameter', 'id': 'max_driving_minutes'}, {'type': 'variable', 'id': 'y', 'indices': ['j']}]}
            }
        },
        'avg_driving_time_target': {
            'description': '사업평균 운전시간 목표',
            'expression_template': 'sum(trip_duration[i] * x[i,j] for i in I) <= avg_driving_target_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['avg_driving_target_minutes'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_avg_driving',
            'penalty_weight': 10
        },
        'max_work_time': {
            'description': '1사업 최대 근무시간 (구속시간)',
            'expression_template': 'duty_end[j] - duty_start[j] <= max_work_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['max_work_minutes'],
            'default_category': 'hard',
            'fixed_category': False,
            'structured': {
                'lhs': {'type': 'subtract', 'terms': [
                    {'type': 'variable', 'id': 'duty_end', 'indices': ['j']},
                    {'type': 'variable', 'id': 'duty_start', 'indices': ['j']}
                ]},
                'operator': '<=',
                'rhs': {'type': 'product', 'terms': [{'type': 'parameter', 'id': 'max_work_minutes'}, {'type': 'variable', 'id': 'y', 'indices': ['j']}]}
            }
        },
        'max_wait_time': {
            'description': '1사업 최대 대기시간',
            'expression_template': '(duty_end[j] - duty_start[j] - sum(trip_duration[i] * x[i,j] for i in I)) <= max_wait_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['max_wait_minutes'],
            'default_category': 'hard',
            'fixed_category': False,
            'context_param': 'max_wait_minutes'
        },
        'avg_wait_time_target': {
            'description': '사업평균 대기시간 목표',
            'expression_template': '(duty_end[j] - duty_start[j] - sum(trip_duration[i] * x[i,j] for i in I)) <= avg_wait_target_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['avg_wait_target_minutes'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_avg_wait',
            'penalty_weight': 5
        },
        'max_total_stay_time': {
            'description': '1사업 최대 체재시간',
            'expression_template': 'duty_end[j] - duty_start[j] + preparation_minutes + cleanup_minutes <= max_total_stay_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['max_total_stay_minutes', 'preparation_minutes', 'cleanup_minutes'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_total_stay',
            'penalty_weight': 8,
            'context_param': 'max_total_stay_minutes'
        },

        # === 준비/정리/휴식 ===
        'preparation_time': {
            'description': '승무 전 준비시간',
            'expression_template': 'duty_start[j] <= trip_dep_time[i] - preparation_minutes + big_m * (1 - x[i,j])',
            'for_each': 'i in I, j in J',
            'parameters': ['preparation_minutes', 'big_m'],
            'default_category': 'hard',
            'fixed_category': True,
            'structured': {
                'lhs': {'type': 'variable', 'id': 'duty_start', 'indices': ['j']},
                'operator': '<=',
                'rhs': {'type': 'expression', 'expr': 'trip_dep_time[i] - preparation_minutes + big_m * (1 - x[i,j])'}
            }
        },
        'cleanup_time': {
            'description': '승무 후 정리시간',
            'expression_template': 'duty_end[j] >= trip_arr_time[i] + cleanup_minutes - big_m * (1 - x[i,j])',
            'for_each': 'i in I, j in J',
            'parameters': ['cleanup_minutes', 'big_m'],
            'default_category': 'hard',
            'fixed_category': True,
            'structured': {
                'lhs': {'type': 'variable', 'id': 'duty_end', 'indices': ['j']},
                'operator': '>=',
                'rhs': {'type': 'expression', 'expr': 'trip_arr_time[i] + cleanup_minutes - big_m * (1 - x[i,j])'}
            }
        },
        'mandatory_break': {
            'description': '필수 휴식시간',
            'expression_template': '(duty_end[j] - duty_start[j]) - sum(trip_duration[i] * x[i,j] for i in I) >= min_break_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['min_break_minutes'],
            'default_category': 'hard',
            'fixed_category': False,
            'structured': {
                'lhs': {'type': 'subtract', 'terms': [
                    {'type': 'subtract', 'terms': [
                        {'type': 'variable', 'id': 'duty_end', 'indices': ['j']},
                        {'type': 'variable', 'id': 'duty_start', 'indices': ['j']}
                    ]},
                    {'type': 'sum', 'variable': 'x', 'sum_over': 'i', 'indices': ['i', 'j'], 'coefficient': 'trip_duration[i]'}
                ]},
                'operator': '>=',
                'rhs': {'type': 'product', 'terms': [{'type': 'parameter', 'id': 'min_break_minutes'}, {'type': 'variable', 'id': 'y', 'indices': ['j']}]}
            }
        },
        'meal_break_guarantee': {
            'description': '식사시간 보장',
            'expression_template': '(duty_end[j] - duty_start[j]) - sum(trip_duration[i] * x[i,j] for i in I) >= min_meal_break_minutes * y[j]',
            'for_each': 'j in J',
            'parameters': ['min_meal_break_minutes'],
            'default_category': 'hard',
            'fixed_category': False
        },

        # === 야간 근무 ===
        'day_duty_start': {
            'description': '주간 근무 시작 시각 제한',
            'expression_template': 'duty_start[j] >= day_duty_start_earliest * (1 - is_night[j])',
            'for_each': 'j in J',
            'parameters': ['day_duty_start_earliest'],
            'default_category': 'hard',
            'fixed_category': False
        },
        'day_duty_end': {
            'description': '주간 근무 종료 시각 제한',
            'expression_template': 'duty_end[j] <= day_duty_end_latest + big_m * is_night[j]',
            'for_each': 'j in J',
            'parameters': ['day_duty_end_latest', 'big_m'],
            'default_category': 'hard',
            'fixed_category': False
        },
        'night_duty_start': {
            'description': '야간 근무 최소 시작 시각 (hard 한계)',
            'expression_template': 'duty_start[j] >= night_duty_start_earliest_min * is_night[j]',
            'for_each': 'j in J',
            'parameters': ['night_duty_start_earliest_min'],
            'default_category': 'hard',
            'fixed_category': False,
            'note': 'hard: 최소 17:00 이후'
        },
        'night_duty_start_preferred': {
            'description': '야간 근무 권장 시작 시각',
            'expression_template': 'duty_start[j] >= night_duty_start_earliest * is_night[j] - slack_night_start[j]',
            'for_each': 'j in J',
            'parameters': ['night_duty_start_earliest'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_night_start',
            'penalty_weight': 15,
            'note': 'soft: 가급적 18:00 이후'
        },
        'day_night_classification': {
            'description': '주간/야간 분류 기준',
            'expression_template': 'duty_start[j] >= night_threshold * is_night[j]',
            'for_each': 'j in J',
            'parameters': ['night_threshold'],
            'default_category': 'hard',
            'fixed_category': False
        },
        'night_sleep_guarantee': {
            'description': '야간 수면시간 보장',
            'expression_template': 'big_m * (1 - is_night[j]) + (duty_end[j] - duty_start[j]) >= min_night_sleep_minutes * is_night[j]',
            'for_each': 'j in J',
            'parameters': ['min_night_sleep_minutes', 'big_m'],
            'default_category': 'hard',
            'fixed_category': False
        },
        'night_rest': {
            'description': '야간 휴식시간 확보',
            'expression_template': '(duty_end[j] - duty_start[j]) - sum(trip_duration[i]*x[i,j] for i in I) >= min_night_rest_minutes * is_night[j]',
            'for_each': 'j in J',
            'parameters': ['min_night_rest_minutes'],
            'default_category': 'hard',
            'fixed_category': False
        },

        # === 도착 후 휴식/교육 ===
        'post_arrival_rest': {
            'description': '도착 후 휴식시간 확보',
            'expression_template': 'post_arrival_rest >= post_arrival_rest_minutes_min',
            'for_each': 'j in J',
            'parameters': ['post_arrival_rest_minutes', 'post_arrival_rest_minutes_min'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_post_rest',
            'penalty_weight': 5,
            'note': '2-3시간 확보 노력 (최소 1시간은 hard)'
        },
        'post_shift_training': {
            'description': '퇴근 후 교육시간 확보',
            'expression_template': 'post_shift_free_time >= post_arrival_rest_minutes_2',
            'for_each': 'j in J',
            'parameters': ['post_arrival_rest_minutes_2'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_training',
            'penalty_weight': 3
        },

        # === 균형/최적화 ===
        'workload_balance': {
            'description': 'crew간 트립 배분 균형',
            'expression_template': 'sum(x[i,j] for i in I) <= max_trips_per_crew * y[j]',
            'for_each': 'j in J',
            'parameters': ['max_trips_per_crew'],
            'default_category': 'soft',
            'fixed_category': False,
            'penalty_var': 'slack_balance',
            'penalty_weight': 5,
            'note': '자동 계산: ceil(|I|/target_crews) + margin'
        }
    },

    'objective': {
        'primary': {
            'type': 'minimize',
            'expression': 'sum(y[j] for j in J)',
            'description': '총 crew 수 최소화'
        },
        'with_soft_penalties': {
            'type': 'minimize',
            'expression': 'sum(y[j] for j in J) + sum(weight_k * slack_k for k in soft_constraints)',
            'description': 'crew 수 최소화 + soft 제약 위반 페널티'
        }
    }
}

# Write YAML
with open(src, 'w', encoding='utf-8') as f:
    yaml.dump(constraints_v3, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)

# Verify
with open(src, encoding='utf-8') as f:
    loaded = yaml.safe_load(f)

n_constraints = len(loaded.get('constraints', {}))
hard_fixed = sum(1 for c in loaded['constraints'].values() if c.get('fixed_category') and c.get('default_category') == 'hard')
hard_default = sum(1 for c in loaded['constraints'].values() if not c.get('fixed_category') and c.get('default_category') == 'hard')
soft_default = sum(1 for c in loaded['constraints'].values() if c.get('default_category') == 'soft')

print(f'constraints.yaml v3 saved successfully')
print(f'  Total constraints: {n_constraints}')
print(f'  Fixed hard: {hard_fixed}')
print(f'  Default hard: {hard_default}')
print(f'  Default soft: {soft_default}')
print(f'  Variables: {len(loaded.get("variables", {}))}')
print(f'  Sets: {len(loaded.get("sets", {}))}')
print()
print('Constraint list:')
for name, c in loaded['constraints'].items():
    cat = c.get('default_category', '?')
    fixed = '(fixed)' if c.get('fixed_category') else ''
    print(f'  [{cat.upper():4s}] {fixed:8s} {name}')
