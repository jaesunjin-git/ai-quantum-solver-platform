---
paths:
  - "domains/**"
  - "knowledge/**"
---

# 도메인 확장 규칙

## 확장 방식
- 새 도메인 = Knowledge Pack(YAML) + `domains/{name}/` 스킬 (코어 수정 불필요)
- 공통 스킬 재사용: `domains/common/skills/` (solver, analyze, math_model 등)

## YAML 지식팩 필수 구조
```
knowledge/domains/{name}/
  ├── _index.yaml              # 도메인 메타 + 제약 목록
  ├── constraints.yaml         # 제약조건 + 목적함수 정의
  ├── parameter_catalog.yaml   # 파라미터 정의 (단일 진실 공급원)
  ├── policies.yaml            # 도메인별 규칙 선언
  ├── reference_ranges.yaml    # 참고 범위
  └── generator_config.yaml    # column generator 설정
```
- version 필드 필수 (호환성 검증에 사용)

## 자동 주입/확장 패턴
1. 기존 데이터/config에서 자동 판별 가능한 방법을 먼저 설계
2. 불가능한 경우에만 config에 명시적 선언 추가
3. config에도 넣을 수 없는 경우에만 코드에 넣되, 주석으로 사유 기록

## 하드코딩 금지
- 새로운 case 추가 시 if/elif 분기 처리 금지
- config 추가만으로 동작해야 함
- engine/ 내부에 특정 도메인(railway, crew 등) 이름/값이 나타나면 안 됨

## LLM 연동 코드 규칙
- 프롬프트 수정 시: 변경 전/후를 사용자에게 보여주고 확인
- 프롬프트 내 도메인 특화 용어 직접 기술 금지 → YAML에서 주입
- LLM 응답 파싱 로직 변경 시: 기존 응답 샘플로 regression 확인
- temperature, max_tokens 등 LLM 파라미터도 config 외부화 대상
