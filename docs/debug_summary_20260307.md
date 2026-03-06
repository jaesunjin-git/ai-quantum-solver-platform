# === 2026-03-07 작업 정리 ===

## 오늘 달성한 것
1. CP-SAT 솔버로 crew scheduling 문제 해결 성공
   - 31명 crew로 320개 trip 100% 커버 (objective=31)
   - 14개 hard constraint + crew_activation_linking 적용
   - constraint_violations = 0

## 현재 정상 작동하는 제약 (15개)
1. trip_coverage: 모든 trip이 정확히 1명에 배정
2. max_driving_time: crew당 운전시간 <= 300분
3. max_work_time: crew당 근무시간 <= 660분
4. preparation_time: 출발 전 준비시간 40분
5. cleanup_time: 도착 후 정리시간 30분
6. mandatory_break: 최소 휴게시간 40분
7. meal_break_guarantee: 식사 휴게시간 40분
8. night_rest: 야간 최소 휴식시간 320분
9. max_total_stay_time: 총 체류시간 <= 720분
10. day_duty_start: 주간 근무 시작 >= 266분 (04:26)
11. day_duty_end: 주간 근무 종료 <= 1380분 (23:00)
12. night_duty_start: 야간 근무 시작 >= 1080분 (18:00)
13. day_night_classification: 야간 분류 기준 >= 1020분 (17:00)
14. night_sleep_guarantee: 야간 최소 수면시간 240분
15. crew_activation_linking: y[j] >= x[i,j] (활성화 연결)

## 미해결 문제 (우선순위 순)

### P1. overlap(no_overlap) 제약 미적용
- 상태: DB에 추가했으나 아직 테스트 안함
- 의미: 시간이 겹치는 trip을 같은 crew에 배정 불가 (x[i1,j]+x[i2,j]<=1)
- 영향: 현재 결과에서 같은 crew에 겹치는 trip이 배정되었을 수 있음
- 예상 제약 수: 3,323 pairs x 96 crews = 319,008개
- 해결: 서버 재시작 후 테스트 실행, 컴파일 시간/feasibility 확인

### P2. expression_parser CP-SAT 호환 불완전
- 상태: line 206 solver->model 수정했으나, 일부 제약에서 여전히 실패
- 영향: expression_parser 실패 시 structured JSON fallback으로 동작
- 원인: expression_parser가 LP용 solver.Add()로 작성됨
- 해결: expression_parser.py 인자명 model_or_solver 통일 완료,
        but 호출부 일부 미반영 가능성 있음 -> 전수 검사 필요

### P3. trip_dep_time[i2]/trip_arr_time[i1] 매핑 경고
- 상태: WARNING 지속 발생
- 원인: struct_builder.get_param_indexed()에서 i1,i2 키를 trip_id로 매핑 못함
- 영향: overlap 관련 제약의 structured JSON 파싱 실패
- 해결: no_overlap(x[i1,j]+x[i2,j]<=1)으로 대체하면 파라미터 참조 불필요

### P4. max_wait_time 제약 timeout
- 상태: LP에서 120초 timeout, CP-SAT 미테스트
- 의미: crew당 총 대기시간 <= 300분
- 해결: no_overlap 적용 후 별도로 추가 테스트

### P5. soft constraint 미구현
- 상태: 0개 (목표: 5~10개)
- 필요: workload_balance, minimize_deadhead, first_second_half_balance 등
- 해결: constraints.yaml에 soft 템플릿 추가, objective에 penalty term 연결

### P6. driving_efficiency 낮음 (20%)
- 상태: crew당 평균 운전 100.8분, 대기 402분
- 원인: overlap 제약 미적용으로 trip 분산이 비효율적
- 해결: no_overlap 적용 후 자연스럽게 개선 기대

### P7. Set J 크기 (96 vs 목표 55~80)
- 상태: J=96 유지
- 해결: 결과 안정화 후 축소 테스트

### P8. 코드 구조 문제
- CP-SAT/LP 경로 이중 관리로 수정 누락 반복
- constraints.yaml에 soft constraint 템플릿 없음
- templates.yaml 변수명(u,d,T,D)과 model 변수명(x,y,I,J) 불일치
- Gate2가 빈 expression 검증 안함
- LLM이 linking 제약을 자동 생성하지 않음

## 오늘 수정한 파일
- engine/compiler/ortools_compiler.py (CP-SAT overlap loader, solver->model)
- engine/compiler/expression_parser.py (인자명 통일)
- engine/compiler/base.py (source_type=file 핸들러)
- engine/compiler/struct_builder.py
- engine/gates/gate2_model_validate.py (for_each 파싱)
- engine/math_model_generator.py
- engine/result_interpreter.py (y[j] 기반 파싱)
- engine/solver_pipeline.py
- uploads/94/normalized/parameters.csv (night_threshold, day_duty_start_earliest)
- uploads/94/normalized/trips.csv (midnight crossing fix)
- DB: core.session_states project_id=94 math_model 다수 수정

## 내일 작업 추천 순서
1. no_overlap 테스트 실행 -> feasibility/성능 확인
2. 결과 해석 검증 (겹치는 trip 배정 여부)
3. max_wait_time 추가 테스트
4. soft constraint 1~2개 추가 (workload_balance)
5. UX 작업
