# Ambiguity Detection & User Clarification Framework

## 배경

최적화 문제 정의 시, 데이터에서 추출한 파라미터가 모호하거나 불충분한 경우가 있다.
현재는 이런 경우 도메인 기본값을 자동 적용하거나 `user_input_required`로 표시만 하고,
**사용자에게 명시적으로 확인을 받는 단계가 없다.**

이로 인해 잘못된 가정으로 모델이 구성되어 실행 결과가 부정확해질 수 있다.

## 핵심 원칙

1. **데이터가 모호하면 가정하지 말고 물어본다**
2. **모든 도메인에 공통 적용** — railway, logistics, finance 등
3. **규칙은 YAML로 외부화** — 도메인별 규칙 추가가 쉬워야 함
4. **질문은 최소화** — 데이터에서 확정 가능한 것은 묻지 않음

## 현재 파이프라인 vs 개선안

### AS-IS
```
데이터 업로드 → 정규화 → _collect_parameters() → 문제정의 제안 → 확정
                                                    ↑ 모호해도 그냥 진행
```

### TO-BE
```
데이터 업로드 → 정규화 → _collect_parameters()
    → _detect_ambiguities(params, data_facts)
    → 질문 있으면 → 대화형 확인 (1~N개 질문)
    → 사용자 답변 → 파라미터 확정 + 패턴 결정
    → 문제정의 제안 → 확정
```

## 모호성 유형

### Type 1: 운영 패턴 모호 (Pattern Ambiguity)
데이터만으로 운영 방식을 판단할 수 없는 경우.

**예시**: 새벽 04:00 DIA가 존재 → 야간 숙박조? 주간 첫차 조기출근?
- trigger: `earliest_trip_start < 360` (06:00 이전)
- 질문: "새벽 시간대 DIA가 있습니다. 야간 숙박조가 운행하나요?"
- 영향: `is_overnight_crew`, `sleep_counts_as_work`, 제약조건 패턴 전환

### Type 2: 필수 파라미터 누락 (Missing Required)
최적화에 필수인 파라미터가 데이터에 없는 경우.

**예시**: `day_crew_count`, `night_crew_count` 미제공
- trigger: 파라미터 source가 없고 도메인 기본값도 없음
- 질문: "주간/야간 승무원 수를 지정해주세요"

### Type 3: 값 범위 이상 (Out-of-Range)
추출된 값이 도메인 기준 범위를 벗어나는 경우.

**예시**: `max_work_minutes = 720` → 일반적 범위 480~600
- trigger: `value outside typical_range`
- 질문: "{param}이 {value}분으로 설정됩니다 (일반: {range}). 맞나요?"

### Type 4: 도메인 기본값 적용 확인 (Default Confirmation)
중요한 파라미터에 기본값이 적용되었을 때 확인.

**예시**: `night_duty_start_earliest = 1020` (17:00, 도메인 기본값)
- trigger: 중요 파라미터이고 source가 default/reference_ranges
- 질문: "야간 최소 출고시간이 17:00로 설정됩니다. 맞나요?"
- 중요도가 낮은 파라미터는 묻지 않음 (importance 필드로 제어)

### Type 5: 파라미터 간 충돌 (Cross-Parameter Conflict)
두 파라미터의 조합이 논리적으로 모순되는 경우.

**예시**: 새벽 DIA 존재 + `night_duty_start_earliest = 1020`
- trigger: cross_rules.yaml 기반 조건 위반
- 질문: "새벽 DIA가 있지만 야간 시작이 17:00입니다. 숙박조 패턴으로 조정할까요?"

## 규칙 파일 구조

```yaml
# knowledge/domains/{domain}/ambiguity_rules.yaml
rules:
  # 규칙 ID
  overnight_crew_pattern:
    # 트리거 조건 (Python eval 가능한 표현식)
    trigger:
      data_condition: "earliest_trip_start < 360"
      description: "06:00 이전 DIA가 존재"

    # 중요도: critical(반드시 물음), high(권장), low(생략 가능)
    importance: critical

    # 질문 체인 (순서대로, 조건부 follow-up)
    questions:
      - id: is_overnight
        text: "새벽 시간대(04:00~06:00) 운행 DIA가 있습니다. 야간 숙박조가 운행하나요?"
        type: yes_no
        on_yes:
          set_params:
            is_overnight_crew: true
          follow_up: [overnight_sleep, sleep_as_work]
        on_no:
          set_params:
            is_overnight_crew: false

      - id: overnight_sleep
        text: "숙박조의 최소 수면시간은 몇 시간인가요?"
        type: numeric
        unit: hours
        default: 6
        param: min_overnight_sleep_minutes
        transform: "value * 60"  # hours → minutes

      - id: sleep_as_work
        text: "수면시간은 근무시간에서 제외하나요?"
        type: yes_no
        on_yes:
          set_params:
            sleep_counts_as_work: false
        on_no:
          set_params:
            sleep_counts_as_work: true

  crew_count_missing:
    trigger:
      param_condition: "day_crew_count.source == null AND night_crew_count.source == null"
      description: "주간/야간 승무원 수 미제공"
    importance: critical
    questions:
      - id: crew_counts
        text: "주간/야간 승무원 수가 데이터에 없습니다. 지정해주세요."
        type: multi_input
        fields:
          - {id: day_crew_count, label: "주간 승무원 수", type: numeric}
          - {id: night_crew_count, label: "야간 승무원 수", type: numeric}

  param_out_of_range:
    trigger:
      param_condition: "any_param_outside_typical_range"
      description: "파라미터 값이 일반 범위를 벗어남"
    importance: high
    questions:
      - id: confirm_value
        text: "{param_name}이 {value}{unit}로 설정됩니다. (일반 범위: {typical_min}~{typical_max}{unit}). 맞나요?"
        type: yes_no
        dynamic: true  # 해당하는 파라미터마다 반복 생성
        on_no:
          action: request_input
          text: "올바른 값을 입력해주세요."
```

## 구현 컴포넌트

### 1. AmbiguityDetector (core/platform 또는 domains/common)
```python
class AmbiguityDetector:
    def __init__(self, domain: str):
        self.rules = load_yaml(f"knowledge/domains/{domain}/ambiguity_rules.yaml")

    def detect(self, parameters: dict, data_facts: dict) -> list[AmbiguityQuestion]:
        """파라미터와 데이터 팩트를 분석하여 질문 목록 반환"""

    def apply_answers(self, answers: dict, parameters: dict) -> dict:
        """사용자 답변을 파라미터에 반영"""
```

### 2. data_facts 구조
```python
data_facts = {
    "earliest_trip_start": 270,      # 04:30 (분)
    "latest_trip_end": 1380,         # 23:00 (분)
    "total_trips": 45,
    "trip_time_distribution": {...},  # 시간대별 분포
    "has_dawn_trips": True,          # 06:00 이전 trip 존재
    "has_late_night_trips": True,    # 23:00 이후 trip 존재
}
```

### 3. 대화 흐름 연동
- `problem_definition.py`의 `handle()` 메서드에 상태 추가:
  - `state.pending_clarifications`: 미확인 질문 목록
  - `state.clarification_answers`: 사용자 답변 누적
- 질문이 있으면 문제정의 제안 전에 질문 대화 먼저 진행
- 모든 질문 답변 완료 후 → 파라미터 확정 → 문제정의 제안

### 4. 프론트엔드 표시
- 질문은 채팅 메시지로 표시 (options 버튼 포함)
- yes_no → 두 버튼
- numeric → 입력 필드
- multi_input → 여러 입력 필드
- 오른쪽 패널에 "확인 대기 중" 상태 표시

## 숙박조 패턴 상세 (첫 번째 구현 케이스)

### 숙박조 타임라인
```
duty_start[j]  (입고, Duty마다 다름 — 옵티마이저가 결정)
    + cleanup_minutes_night (정리시간, 파라미터)
    = sleep_start[j]  (수면 시작, 도출 변수)

first_trip_start[j]  (다음날 첫 운행, 데이터)
    - preparation_minutes_night (준비시간, 파라미터)
    = sleep_end[j]  (기상, 도출 변수)

sleep_duration[j] = sleep_end[j] - sleep_start[j]  (도출 변수)
```

### 관련 파라미터
| 파라미터 | 의미 | 고정/변수 | 출처 |
|---|---|---|---|
| `cleanup_minutes_night` | 입고 후 정리시간 | 파라미터 | 데이터 or 질문 |
| `preparation_minutes_night` | 기상 후 준비시간 | 파라미터 | 데이터 or 질문 |
| `min_overnight_sleep_minutes` | 최소 수면시간 | 파라미터 | 질문 |
| `sleep_counts_as_work` | 수면=근무 여부 | 파라미터 | 질문 |
| `duty_start[j]` | 입고 시각 | **변수** | 옵티마이저 결정 |
| `sleep_duration[j]` | 실제 수면시간 | **도출 변수** | 계산 |

### 제약조건 변경
```
# 수면시간 보장
sleep_duration[j] >= min_overnight_sleep_minutes  (for overnight crew)

# 근무시간 계산 (수면 제외)
work_time[j] = duty_span[j] - sleep_duration[j]  (if sleep_counts_as_work == false)
work_time[j] <= max_work_minutes
```

## 구현 우선순위

1. **Phase 1**: `ambiguity_rules.yaml` 작성 (railway: 숙박조 + 범위이상 + 누락)
2. **Phase 2**: `AmbiguityDetector` 엔진 구현
3. **Phase 3**: `problem_definition.py` 대화 흐름 연동
4. **Phase 4**: 프론트엔드 질문 UI
5. **Phase 5**: 숙박조 제약조건 패턴 구현 + 테스트
6. **Phase 6**: logistics 도메인 규칙 추가
