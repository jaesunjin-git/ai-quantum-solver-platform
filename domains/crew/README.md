# domains/crew — Crew Scheduling 문제 유형

이 모듈은 **승무원 스케줄링(crew scheduling)** 문제 유형을 구현합니다.

## 도메인 지식팩과의 관계

| 구분 | 위치 | 역할 |
|------|------|------|
| **코드 (문제 유형)** | `domains/crew/` (여기) | duty 생성 로직, feasibility 체크, 결과 변환 |
| **지식팩 (산업 도메인)** | `knowledge/domains/railway/` | 제약 정의, 파라미터 카탈로그, 역명/노선 등 도메인 지식 |
| **매핑** | `engine/domain_registry.py` | `railway` → `domains.crew.*` 연결 |

## 설계 의도

- `crew`는 **문제 유형**이고, `railway`는 **산업 도메인**입니다.
- 같은 crew scheduling 로직이 bus, airline 등 다른 산업에서도 재사용 가능합니다.
- 산업별로 달라지는 것(역명, 규정 수치, 노선 구조)은 지식팩 YAML에, 공통 로직은 이 모듈에 있습니다.

## 주요 파일

| 파일 | 역할 |
|------|------|
| `duty_generator.py` | CrewDutyGenerator — 야간/숙박 duty 생성 확장 |
| `result_converter.py` | SP 결과 → 승무원 스케줄 변환 |
| `skills/problem_definition.py` | 문제 정의 워크플로우 |
| `skills/structural_normalization.py` | 데이터 정규화 |
