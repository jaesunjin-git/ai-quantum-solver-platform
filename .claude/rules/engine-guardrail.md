---
paths:
  - "engine/**"
  - "core/**"
---

# Engine/Core 작업 규칙

## 4대 Guardrail

### GR-1. Domain Isolation — 의존성 방향 강제
- core/, engine/은 domains/를 절대 import하지 않음 (단방향: domains → core)
- 도메인 정보는 `SessionState.detected_domain` 문자열로만 전달
- 위반 시 코드 리뷰 차단

### GR-2. Parameter Catalog 중앙화
- 모든 파라미터: `knowledge/domains/{name}/parameter_catalog.yaml`에 정의
- `engine/policy/parameter_catalog.py`가 유일한 resolve 경로
- catalog 미등록 파라미터는 컴파일 거부

### GR-3. Policy Engine Plugin화
- 도메인별 규칙은 `policies.yaml`로만 선언 (Python 코드 아님)
- eval() 금지, 선언적 activation만
- 새 temporal type/derived field 추가 = YAML 선언만으로 가능해야 함

### GR-4. Canonical Model Strictness
- L3 진입: catalog 등록 + type 일치 + range 통과 필수
- 미검증 값이 L4(컴파일) 도달 시 즉시 차단 + 진단 리포트
- confirmed_problem에 추측값 삽입 절대 금지

## 5-Layer 품질 책임
```
L1 AI Interpretation  → hypothesis + confidence 로깅
L2 Semantic Resolution → candidate 제안 + 매핑 추적
L3 Canonical Model     → confirmed 검증 + derived field + provenance
L4 Compilation         → canonical→solver 변환 + 컴파일 리포트
L5 Solver Orchestration → 실행 + 결과 grading + 메트릭 로깅
```
- 각 Layer는 자기 아웃풋 검증 후 다음 Layer 전달 (불합격 시 차단)
- similarity/prefix 매칭은 L1~L2까지만, L3 이후 confirmed data만

## Validation Gates
- Gate1(Data) → Gate2(Model) → Gate3(Compile) → Gate4(Post-Solve)
- 제약조건: Data-confirmed → 자동 | Required+missing → 사용자 확인 | Optional+missing → 필터

## Observability
- 구조화된 로깅: `logger.info("L3:canonical", extra={...})` 패턴
- 데이터 변환 전 구간 추적 필수
- 장애 시 어느 Layer에서 무엇이 잘못되었는지 즉시 파악 가능해야 함

## 상용 운영 원칙
- LLM 장애: Fast-Path fallback + 캐시 응답
- 솔버 장애: 타임아웃 강제 + 자동 취소 + 사용자 안내
- DB/외부 서비스: 커넥션 풀 + health check + circuit breaker
- API 응답: 동기 엔드포인트 3초 이내 목표
