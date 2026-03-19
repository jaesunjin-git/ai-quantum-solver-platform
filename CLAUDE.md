# CLAUDE.md — AI Quantum Solver Platform

> KQC(한국퀀텀컴퓨팅) Quantum Hybrid 전략 기반 최적화 플랫폼
> AI가 사용자 요구사항을 해석하고, 고전/양자 하이브리드 솔버로 문제를 해결하는 플랫폼
> 현재: Crew Scheduling | 향후: Material Science, Finance, Logistics 등 전방위 도메인 확장

---

## 프로젝트 스택

- **백엔드**: Python/FastAPI (루트) | **프론트엔드**: React/Vite/TS (`frontend/`)
- **솔버**: OR-Tools CP-SAT (classical), D-Wave CQM/BQM/NL (quantum/hybrid)
- **DB**: PostgreSQL (Docker), 스키마: core, model, job, data, chat, domain, engine
- **LLM**: Google Gemini (intent classification, math model, analysis)
- **인증**: JWT (python-jose + bcrypt) | **향후**: MSA 아키텍처 전환 예정

---

## 핵심 원칙 (절대 규칙)

### 1. 범용성 & 확장성 최우선
- 모든 코드는 **multi-domain 확장**을 전제로 설계 (crew-specific 하드코딩 금지)
- 도메인 지식은 **YAML 지식팩으로 외부화** (`knowledge/domains/`)
- **하드코딩 절대 금지** — 설정값, 도메인 규칙, 매직넘버 모두 YAML/환경변수로

### 2. 실행 경로에 Heuristic 금지
- similarity/prefix 매칭은 **hypothesis(L1~L2)까지만**
- `confirmed_problem`에는 **confirmed data만** — 추측값 절대 불가
- Suggestion Engine은 실행 경로와 **격리(quarantine)**

### 3. 불명확하면 추측하지 말고 반드시 사용자에게 질문
- 요구사항, 설계 방향, 비즈니스 로직이 불명확할 때 **추측 금지**
- 선택지를 제시하고 확인 후 진행

### 4. Layer별 출력 품질 책임
- 각 Layer는 자신의 **아웃풋 품질을 스스로 검증**하고 다음 Layer에 전달
- 불합격 데이터를 다음 Layer로 넘기지 않음 — **Gate에서 차단**
- Layer 간 계약: 입력 스키마 / 출력 스키마 / 에러 프로토콜 명시

### 5. Observability & 추적성
- 모든 Layer의 입출력은 **로깅 + 프로비넌스 추적** 필수
- 데이터 변환 경로: 원본 → 정규화 → 해석 → 컴파일 → 솔버 결과 전 구간 추적
- 장애 시 **어느 Layer에서 무엇이 잘못되었는지** 즉시 파악 가능해야 함
- 구조화된 로깅: `logger.info("L3:canonical", extra={...})` 패턴

### 6. 버전 관리 전략
- **YAML 지식팩**: `version` 필드 필수 (호환성 검증에 사용)
- **Policy / Model**: 변경 시 버전 bump, 이전 버전과 결과 비교 가능
- **API**: URL prefix versioning (`/api/v1/`) — MSA 전환 시 무중단 배포
- **DB 스키마**: Alembic 마이그레이션 (D4, 예정)

### 7. 데이터 거버넌스
- **생명주기**: 업로드 → 정규화 → 모델 바인딩 → 솔버 → 결과 → 보관/삭제
- **접근 제어**: JWT 역할 기반 (admin/researcher), 프로젝트 소유권 검증
- **민감 데이터**: API 키 Fernet 암호화, `.env` 커밋 금지, 로그에 PII 노출 금지
- **품질**: Gate 통과 데이터만 다음 단계 진입, 원본 데이터 불변 보존

### 8. MSA 전환 대비
- core/, engine/, domains/ 간 **의존성 최소화**, 인터페이스 기반 설계, Stateless 지향

---

## 상용 운영 원칙

### 에러 복구 & 회복탄력성
- **LLM 장애**: Gemini 타임아웃/에러 시 Fast-Path fallback, 캐시 응답 제공
- **솔버 장애**: 실행 타임아웃 강제 + 자동 취소 + 사용자 안내, 재시도는 사용자 승인 후
- **DB/외부 서비스**: 커넥션 풀 + health check + circuit breaker 패턴

### 멀티테넌시
- **데이터 격리**: 테넌트별 프로젝트 소유권 검증 (현재 user_id 기반 → 향후 org_id)
- **리소스 격리**: 테넌트별 솔버 실행 quota, API rate limit 분리
- **설정 격리**: 테넌트별 Knowledge Pack 커스터마이징 허용 (base + override 구조)

### 성능 예산 (SLA)
- 각 Layer/Gate에 **최대 허용 시간** 설정, 초과 시 타임아웃 + 경고
- 솔버 실행: 기본 5분 타임아웃, 대규모 문제는 비동기 Job 전환
- API 응답: 동기 엔드포인트 **3초 이내** 목표

### 감사 추적 (Audit Trail)
- 사용자 행위(로그인, 데이터 업로드, 솔버 실행, 설정 변경) **불변 감사 로그** 기록
- Observability(기술 디버깅)와 **별도 체계** — 감사 로그는 삭제/수정 불가

### LLM 의존성 관리
- **프롬프트 버전 관리**: 프롬프트 템플릿도 YAML + version 필드로 관리
- **비용 모니터링**: 토큰 사용량/비용 추적, 임계치 초과 시 알림
- **응답 캐싱**: 동일 intent 패턴 → 캐시 히트 (LLM 호출 최소화)

### 배포 & 환경
- **환경 분리**: dev → staging → production (설정/시크릿 완전 분리)
- **CI/CD**: PR 머지 → 자동 테스트 → staging 배포 → 승인 후 production
- **무중단 배포**: API 버전 관리 (`/api/v1/`) + health check 기반 롤링 배포

> 상세 → [docs/architecture.md](docs/architecture.md)

---

## 도메인 확장 전략 & Guardrail

### 확장 방식
- **새 도메인 = Knowledge Pack(YAML) + domains/{name}/ 스킬** (코어 수정 불필요)
- **공통 스킬 재사용**: `domains/common/skills/`의 solver, analyze, math_model 등

### 4대 Guardrail (도메인 확장 시 아키텍처 붕괴 방지)

**GR-1. Domain Isolation Layer** — 의존성 방향 강제
- `core/`, `engine/`는 `domains/`를 **절대 import 하지 않음** (단방향: domains → core)
- 도메인 정보는 `SessionState.detected_domain` 문자열로만 전달
- 위반 시: **코드 리뷰 차단** — core/engine에 도메인 특화 로직 금지

**GR-2. Parameter Catalog 중앙화** — 단일 진실 공급원
- 모든 파라미터는 `knowledge/domains/{name}/parameter_catalog.yaml`에 정의
- `engine/policy/parameter_catalog.py`가 유일한 resolve 경로
- 코드 내 파라미터 하드코딩/인라인 정의 금지 → catalog 미등록 파라미터는 컴파일 거부

**GR-3. Policy Engine Plugin화** — 도메인별 규칙 플러그인
- 각 도메인은 `policies.yaml`로만 규칙 선언 (Python 코드 아님)
- PolicyEngine은 도메인 무관한 범용 실행기 — `eval()` 금지, 선언적 activation만
- 새 temporal type/derived field 추가 = YAML 선언만으로 가능해야 함

**GR-4. Canonical Model Strictness** — L3 진입 엄격 검증
- Canonical Model에 진입하는 모든 값은 **catalog 등록 + type 일치 + range 통과** 필수
- 미검증 값이 L4(컴파일)에 도달하면 **즉시 차단 + 진단 리포트**
- confirmed_problem에 추측값/heuristic 결과 삽입 절대 금지

> 상세 (의존성 다이어그램, 위반 탐지, 체크리스트) → [docs/architecture.md](docs/architecture.md)

---

## 작업 프로세스 (필수)

```
① 계획 수립 → ② 사용자 확인 → ③ 개발 → ④ 테스트 → ⑤ 할일 재정리
```

1. **계획 먼저**: 코드 작성 전 반드시 구현 계획을 세우고 사용자 확인
2. **WHY 설명**: 코드 변경 전 변경 이유를 먼저 설명
3. **솔버 실행 확인**: 파일 수정 전 uvicorn --reload 재시작 영향 확인
4. **테스트 필수**: 변경 후 `pytest tests/ -v` 실행하여 검증 완료 후 완료 선언
5. **할일 재정리**: 작업 완료 후 전체 TODO 목록 정리 + 우선순위 재결정
6. **보안 준수**: OWASP Top 10, JWT, CORS 제한, Rate limiting, 암호화

---

## 코딩 표준 (요약)

- **Python**: 클래스 `PascalCase` / 함수 `snake_case` / 상수 `UPPER_SNAKE` / DB모델 `*DB`
- **TS/React**: 컴포넌트 `PascalCase` / 함수 `camelCase` / 훅 `useXxx` / 300줄 이하 분리
- **공통**: 파일/함수 단위 **한국어 주석 필수** / Git 커밋 `feat:`/`fix:`/`chore:` 접두사
- **설정**: YAML (`configs/`) + 환경변수 (`.env`) / 에러: `ErrorCode` enum + `error_response()`

> 상세 → [docs/coding_conventions.md](docs/coding_conventions.md)

---

## 아키텍처 요약

### 5-Layer 구조 (각 Layer가 출력 품질 책임)
```
L1 AI Interpretation  → hypothesis 생성 + confidence 로깅
L2 Semantic Resolution → candidate 제안 + 매핑 추적 (suggestion only)
L3 Canonical Model     → confirmed 검증 + derived field 생성 + provenance
L4 Compilation         → canonical→solver 변환 + 컴파일 리포트 출력
L5 Solver Orchestration → 실행 + 결과 품질 grading + 솔버 메트릭 로깅
```
- **각 Layer는 자기 아웃풋을 검증 후 다음 Layer에 전달** (불합격 시 차단)

### Validation Gates
- Gate1(Data) → Gate2(Model) → Gate3(Compile) → Gate4(Post-Solve, 계획중)

### 제약조건 정책
- Data-confirmed → 자동 적용 | Required+missing → 사용자 확인 | Optional+missing → 필터

> 상세 → [docs/architecture.md](docs/architecture.md)

---

## 현재 상태 & 로드맵

- **완료**: Security(S1~S3) ✅ | Data/Scale(D1~D3) ✅ | Gate1~3 ✅ | Compiler 44x ✅ | **C0 Presolve** ✅
- **P0 (12)**: D3/D4(값검증) → N3(구조검증) → N2(alias이관) → B1~B3(temporal) → H4+C2(가시화+수집) → H5 → C1+C3(E2E)
- **미착수**: D4 Alembic | Multi-Agent(M1~M5) | MSA 전환 | 테스트: 433개 통과

> 로드맵 → [docs/ROADMAP.md](docs/ROADMAP.md) | 할일 → memory/todo_2026_03_19.md

---

## 참조 문서

| 문서 | 내용 |
|------|------|
| [docs/coding_conventions.md](docs/coding_conventions.md) | 코딩 컨벤션 상세 (Python, TS, 테스트, 보안) |
| [docs/architecture.md](docs/architecture.md) | 5-Layer, 품질책임, Observability, 버전관리, 데이터거버넌스, MSA |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 3-Track 진화 로드맵 |
| [docs/constraint_architecture.md](docs/constraint_architecture.md) | 제약조건 아키텍처 |
| [docs/ambiguity_detection_design.md](docs/ambiguity_detection_design.md) | 모호성 감지 설계 |
