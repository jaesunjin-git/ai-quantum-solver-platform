# 제약조건 컴파일 아키텍처 설계 문서

> 상태: Phase 1 진행중 | 최종수정: 2026-02-27

## 1. 현재 문제

LLM이 생성하는 제약조건이 자유 형식 문자열이라 파서 인식률이 11% (2/18).

## 2. 해결: 구조화된 LHS / 연산자 / RHS 스키마

스키마 정의: prompts/schemas/constraint_schema.yaml 참조.

컴파일러 3단계 Fallback:
  1단계: 구조화 필드 (lhs/operator/rhs) -> 솔버 제약
  2단계: expression -> AST 파서 -> 솔버 제약
  3단계: 정규식 패턴 매칭 (최후 수단)

## 3. 설정 파일 구조

  configs/
    classifier_keywords.yaml    의도 분류 키워드
    domain_profiles.yaml        도메인별 프로파일
    solvers/                    솔버 정의 (classical, dwave, ibm, ionq, pasqal)

  prompts/
    crew/
      system.md                 시스템 프롬프트
      analysis_report.md        분석 리포트 템플릿
      math_model.yaml           수학 모델 생성 프롬프트
      consultant.yaml           컨설턴트 프롬프트
      general_chat.yaml         일반 질의 프롬프트
    schemas/
      constraint_schema.yaml    구조화된 제약 스키마

  utils/prompt_loader.py        .md + .yaml 통합 로드

## 4. 양자 솔버 확장 (Phase 2)

QUBO 변환 메타데이터는 컴파일러가 자동 계산.
  - 등식 제약: penalty P*(sum - K)^2
  - 부등식 제약: 슬랙 변수, ceil(log2(K+1)) 큐빗
  - 고차 제약: ancilla 변수로 이차 축소

연구 과제:
  1. 페널티 가중치 자동 튜닝
  2. 제약별 큐빗 임팩트 추정
  3. 하이브리드 고전+양자 분할
  4. CQM vs QUBO 자동 선택
  5. 제약 간 충돌 사전 분석
  6. 도메인별 제약 템플릿 라이브러리

## 5. 변경 이력

  2026-02-27  초안, Phase 1 스키마 확정, 프롬프트 외부화
