# 플랫폼 아키텍처 상세

> CLAUDE.md에서 참조하는 아키텍처 상세 문서

---

## 1. 5-Layer 아키텍처

| Layer | 역할 | 핵심 규칙 |
|-------|------|-----------|
| L1 — AI Interpretation | 자연어 → hypothesis 생성 | **hypothesis만**, confirmed 아님 |
| L2 — Semantic Resolution | similarity → candidate 제안 | **suggestion만**, 실행 경로 진입 불가 |
| L3 — Canonical Model | Policy Engine 변환 | **confirmed data만** 사용 |
| L4 — Compilation | canonical model → solver input | L1~L2 세부사항 무시 |
| L5 — Solver Orchestration | 자동 백엔드 선택 | Automatic ≠ unconditional |

### Layer 간 규칙
- L1→L2: hypothesis를 candidate로 정제, 사용자 확인 필요
- L2→L3: confirmed된 값만 canonical model에 진입
- L3→L4: Policy Engine이 temporal type 등 derived field 생성
- L4→L5: 컴파일된 모델만 솔버에 전달

### Layer별 출력 품질 책임

각 Layer는 자신의 아웃풋 품질을 보장할 책임이 있다. 불합격 데이터는 다음 Layer로 전달하지 않는다.

| Layer | 출력물 | 품질 검증 기준 | 차단 조건 |
|-------|--------|---------------|-----------|
| L1 | hypothesis (intent, entities) | confidence score ≥ threshold | 낮은 confidence → 사용자 재확인 요청 |
| L2 | candidate mappings | catalog 존재 + type 일치 | catalog miss → suggestion으로 격리 |
| L3 | canonical model (confirmed params) | 전수 검증: range, type, 필수값 | range 위반 / 필수값 누락 → Gate 차단 |
| L4 | solver input (compiled model) | 변수/제약 정합성 + CompilePolicy | strict 위반 → 컴파일 거부 + 진단 리포트 |
| L5 | solution + metrics | feasibility + quality grading | INFEASIBLE → 원인 분석 + 사용자 안내 |

**원칙**: 문제가 발생한 Layer에서 해결한다. 하위 Layer가 상위 Layer의 오류를 보정하지 않는다.

---

## 2. 상용 설계 7원칙 (No Heuristic in Execution)

1. **실행 경로에 heuristic 금지** — similarity/prefix 매칭은 hypothesis까지만
2. **confirmed_problem에는 confirmed data만** — 추측값 절대 불가
3. **Parameter Catalog** — family/type/scope 메타데이터 필수
4. **Deterministic Resolver** — 명시적 우선순위 기반
5. **Suggestion Engine은 실행 경로와 격리** — quarantine
6. **Range/Sanity Validation** — temporal type lint 포함
7. **Versioned Provenance** — 출처 추적 + 캐시 무효화

---

## 3. 제약조건 정책

| 상태 | 처리 |
|------|------|
| Data-confirmed | 자동 적용 |
| Optional + not-in-data | 필터 아웃 |
| Required + not-in-data | Ambiguity Detection (사용자 확인) |
| Solver-incompatible | Post-solve validation |

---

## 4. Validation Gates

| Gate | 위치 | 역할 |
|------|------|------|
| Gate 1 | Data Upload | 파일 형식, 필수 컬럼, 데이터 타입 |
| Gate 2 | Model Build | 파라미터 카탈로그, semantic resolution |
| Gate 3 | Compile | CompilePolicy strict/debug, blocking 확대 |
| Gate 4 | Post-Solve | (계획 중) solution quality grading |

---

## 5. Intent Classification (2-tier)

1. **Fast-Path**: 무료, 정확한 버튼 매칭 (`configs/skill_intents.yaml`)
2. **LLM Fallback**: Gemini, confidence ≥ 0.6 게이팅

---

## 6. Policy Engine (Temporal Semantics)

- Temporal types: `raw_clock_minute`, `service_day_minute`, `duration_minute`
- 선언적 activation (no `eval`)
- `policies.yaml` 도메인 지식팩 선언
- Derived fields: `trip_dep_abs_minute`, `trip_arr_abs_minute`, `service_day_index`

---

## 7. 디렉토리 구조

```
├── main.py                    # FastAPI 앱, lifespan, router 등록
├── core/
│   ├── config.py              # Settings 클래스, 환경변수
│   ├── database.py            # SQLAlchemy engine, SessionLocal, Base
│   ├── models.py              # ORM 모델 (스키마 한정)
│   ├── auth.py                # JWT 생성/검증
│   ├── auth_router.py         # /api/auth/* 라우트
│   ├── crypto.py              # Fernet 대칭 암호화
│   ├── job_router.py          # /api/jobs/* 라우트
│   └── platform/
│       ├── errors.py          # ErrorCode enum, error_response()
│       ├── session.py         # SessionState 관리
│       ├── classifier.py      # Intent 분류
│       ├── ambiguity_detector.py  # 범용 모호성 감지
│       ├── stage_manager.py   # YAML 파이프라인 전이
│       ├── intent_classifier.py   # Fast-Path + LLM 분류
│       └── utils.py
├── domains/
│   ├── common/skills/         # 공통 스킬 (solver, analyze, math_model 등)
│   └── crew/                  # 승무원 도메인 (agent.py, skills/)
├── engine/
│   ├── compiler/              # affine_collector.py, errors.py
│   ├── policy/                # PolicyEngine, temporal types
│   ├── tasks.py               # Celery 태스크
│   └── post_processing.py     # 후처리 공통 헬퍼
├── configs/                   # YAML 설정 (pipeline, intents, solvers)
├── knowledge/domains/         # 도메인 지식팩 (railway, logistics)
├── tests/                     # pytest 테스트
└── frontend/                  # React/Vite/TS 앱
    └── src/
        ├── components/        # UI 컴포넌트
        ├── context/           # React Context (Auth, Project)
        └── hooks/             # Custom hooks
```

---

## 8. Observability & 추적성

### 8.1 구조화된 로깅 원칙

모든 Layer의 입출력은 로깅 + 프로비넌스 추적이 필수이다. 장애 시 어느 Layer에서 무엇이 잘못되었는지 즉시 파악 가능해야 한다.

```python
# 로깅 패턴 예시
logger.info("L3:canonical:param_resolved", extra={
    "session_id": sid,
    "param_name": "max_duty_hours",
    "source": "user_confirmed",      # provenance: user_confirmed | catalog_default | policy_derived
    "value": 9,
    "version": "1.2.0",
})
```

### 8.2 추적 대상

| 구간 | 추적 항목 | 목적 |
|------|----------|------|
| L1 입출력 | 원문, intent, confidence, entities | 분류 오류 진단 |
| L2 매핑 | candidate list, 선택 근거, catalog hit/miss | semantic 오류 진단 |
| L3 정규화 | 파라미터 출처(provenance), 변환 전/후 값 | 데이터 변환 추적 |
| L4 컴파일 | 변수/제약 수, 컴파일 시간, 경고/에러 | 컴파일 성능/품질 |
| L5 솔버 | 솔버 선택 근거, 실행 시간, 결과 상태, 품질 지표 | 솔버 성능 모니터링 |
| 전 구간 | session_id, timestamp, user_id | 엔드-투-엔드 추적 |

### 8.3 프로비넌스 (Provenance)

모든 confirmed 파라미터는 출처를 기록한다:

| 출처 타입 | 설명 | 예시 |
|-----------|------|------|
| `user_input` | 사용자가 직접 입력/확인 | 채팅으로 "최대 9시간" 응답 |
| `data_extracted` | 업로드 데이터에서 추출 | CSV 컬럼에서 자동 감지 |
| `catalog_default` | Parameter Catalog 기본값 | YAML에 정의된 default |
| `policy_derived` | Policy Engine이 계산 | temporal type 변환 결과 |
| `ambiguity_resolved` | 모호성 감지 후 사용자 확인 | clarification 응답 |

### 8.4 향후 계획
- 구조화 로그 → ELK/Grafana 연동 (MSA 전환 시)
- 분산 추적: OpenTelemetry trace_id 전파
- 실시간 대시보드: Layer별 처리량, 에러율, 지연시간

---

## 9. 버전 관리 전략

### 9.1 버전 대상 및 정책

| 대상 | 버전 형식 | 위치 | 변경 시 규칙 |
|------|----------|------|-------------|
| YAML 지식팩 | `version: "1.2.0"` (SemVer) | 각 YAML 파일 상단 | breaking change → major bump |
| Policy 규칙 | `version` 필드 | `policies.yaml` | 파생 필드 변경 → minor bump |
| Parameter Catalog | `version` 필드 | `parameter_catalog.yaml` | 파라미터 추가 → minor, 삭제 → major |
| Math Model | 모델 해시 또는 버전 태그 | DB `model` 스키마 | 구조 변경 시 새 버전 생성 |
| API | URL prefix `/api/v1/` | 라우터 prefix | breaking change → v2 신설 (v1 유지) |
| DB 스키마 | Alembic revision | `alembic/versions/` | 모든 변경은 마이그레이션 스크립트 |
| 솔버 설정 | `version` 필드 | `configs/solvers/*.yaml` | 솔버 파라미터 변경 시 bump |

### 9.2 호환성 원칙
- **하위 호환**: minor/patch 버전은 기존 데이터와 호환 필수
- **마이그레이션**: major 버전 변경 시 마이그레이션 경로 제공
- **비교 가능**: 동일 입력 + 다른 정책 버전 → 결과 비교 가능해야 함
- **롤백 가능**: 이전 버전으로 되돌릴 수 있는 구조 유지

### 9.3 YAML 지식팩 버전 예시
```yaml
# knowledge/domains/railway/constraints.yaml
version: "1.3.0"      # ← 필수
domain: railway
last_updated: 2026-03-19
constraints:
  - name: max_duty_hours
    ...
```

---

## 10. 데이터 거버넌스

### 10.1 데이터 생명주기

```
업로드(원본) → 검증(Gate1) → 정규화 → 바인딩(Gate2) → 컴파일(Gate3)
    → 솔버 실행 → 결과(Gate4) → 보관/시각화 → 만료/삭제
```

| 단계 | 상태 | 규칙 |
|------|------|------|
| 원본 | `uploads/{project_id}/` | **불변 보존** — 정규화 결과는 별도 저장 |
| 정규화 | DB `data` 스키마 | 원본 대비 변환 이력 기록 |
| 모델 바인딩 | DB `model` 스키마 | confirmed data만, provenance 포함 |
| 솔버 결과 | DB `job` 스키마 | 입력 스냅샷 + 결과 + 메트릭 |
| 보관 | DB + 파일 | 프로젝트 삭제 시 연쇄 정리 (`uploads/` 포함) |

### 10.2 접근 제어

| 역할 | 권한 |
|------|------|
| admin | 전체 프로젝트 CRUD, 사용자 관리, 솔버 설정 |
| researcher | 자기 프로젝트만 CRUD, 솔버 실행 |

- JWT 토큰 기반 인증 (24시간 만료)
- 프로젝트 소유권 검증: `project.user_id == current_user.id`
- 향후: RBAC 세분화, 팀/조직 단위 접근 제어

### 10.3 민감 데이터 보호

| 대상 | 보호 방식 |
|------|----------|
| DB 비밀번호 | 환경변수 (`DATABASE_URL`), `.env` 커밋 금지 |
| API 키 (D-Wave 등) | Fernet 대칭 암호화 (`core/crypto.py`) |
| JWT Secret | 환경변수 (`JWT_SECRET_KEY`) |
| 사용자 비밀번호 | bcrypt 해시 (plaintext 저장 금지) |
| 로그 | PII (개인식별정보) 출력 금지, 토큰 마스킹 |

### 10.4 데이터 품질 규칙
- **Gate 통과 데이터만** 다음 단계 진입 (4-Gate 체계)
- **원본 불변**: 업로드된 파일은 절대 수정하지 않음
- **변환 추적**: 정규화/파생 필드 생성 시 변환 로그 기록
- **정합성 검증**: 파라미터 range, type, 필수값 전수 검증 (L3)
- **결과 재현성**: 동일 입력 + 동일 버전 → 동일 결과 보장

---

## 11. 도메인 확장 전략 & Guardrail

### 11.1 Knowledge Pack 기반 확장

새 도메인 추가 시 코어 엔진 수정 없이 YAML 팩 + 도메인 스킬만 추가:

```
knowledge/domains/{domain_name}/
├── _index.yaml              # 도메인 프로필 (이름, 설명, 버전)
├── constraints.yaml          # 제약조건 정의 (version 필수)
├── ambiguity_rules.yaml      # 모호성 감지 규칙
├── policies.yaml             # Policy Engine 규칙 (version 필수)
├── parameter_catalog.yaml    # 파라미터 카탈로그 (version 필수)
└── domain_aliases.yaml       # 별칭 매핑

domains/{domain_name}/
├── skills/                   # 도메인 특화 스킬
└── agent.py                  # 도메인 오케스트레이터
```

### 11.2 확장 로드맵

| 도메인 | 유형 | 양자 활용 포인트 | 상태 |
|--------|------|-----------------|------|
| Railway (Crew Scheduling) | 조합최적화 | 대규모 순열/스케줄링 | **개발 중** |
| Logistics (VRP/TSP) | 경로최적화 | TSP/VRP 양자 가속 | 지식팩 준비 완료 |
| Finance (Portfolio) | 포트폴리오최적화 | QUBO 기반 자산 배분 | 예정 |
| Material Science | 분자 시뮬레이션 | 양자 화학 계산 | 예정 |

### 11.3 4대 Guardrail — 도메인 확장 시 아키텍처 붕괴 방지

---

#### GR-1. Domain Isolation Layer — 의존성 방향 강제

도메인 코드가 코어/엔진에 침투하는 것을 원천 차단한다.

**의존성 방향 다이어그램 (단방향 only)**:
```
┌─────────────────────────────────────────────────┐
│  domains/{name}/                                │  ← 도메인 특화 (최상위)
│    skills/, agent.py                            │
│    ↓ imports from                               │
├─────────────────────────────────────────────────┤
│  domains/common/skills/                         │  ← 공통 스킬
│    solver, analyze, math_model, handlers        │
│    ↓ imports from                               │
├─────────────────────────────────────────────────┤
│  core/platform/  +  engine/                     │  ← 도메인 무관 코어
│    session, classifier, ambiguity_detector      │
│    compiler, policy_engine, parameter_catalog   │
│    ↓ reads from (YAML, 동적 로딩)                │
├─────────────────────────────────────────────────┤
│  knowledge/domains/{name}/                      │  ← 도메인 지식 (YAML only)
│    constraints, policies, parameter_catalog     │
└─────────────────────────────────────────────────┘
```

**규칙**:
| 규칙 | 설명 | 위반 예시 |
|------|------|----------|
| **R-1** | `core/` → `domains/` import 금지 | `from domains.crew import ...` in core/ |
| **R-2** | `engine/` → `domains/` import 금지 | `from domains.crew.skills import ...` in engine/ |
| **R-3** | 도메인 전달은 **문자열만** | `SessionState.detected_domain = "railway"` (객체 아님) |
| **R-4** | 도메인별 분기는 **YAML 로딩으로만** | `if domain == "crew":` 같은 하드코딩 금지 |
| **R-5** | 공통 스킬은 특정 도메인 import 금지 | `from domains.crew import ...` in common/ |

**위반 탐지 방법**:
```bash
# CI/PR 검증용 — core/engine에서 domains 직접 import 탐지
grep -rn "from domains\." core/ engine/ --include="*.py"
grep -rn "import domains\." core/ engine/ --include="*.py"
# 결과가 0건이어야 통과
```

---

#### GR-2. Parameter Catalog 중앙화 — 단일 진실 공급원 (Single Source of Truth)

모든 파라미터의 정의, 검증, 기본값은 반드시 Parameter Catalog를 거친다.

**구조**:
```
knowledge/domains/{name}/parameter_catalog.yaml   ← 정의 (YAML)
        ↓ 로딩
engine/policy/parameter_catalog.py                 ← 유일한 resolve 경로
        ↓ 제공
data_binder.py, compiler, policy_engine            ← 소비자
```

**규칙**:
| 규칙 | 설명 |
|------|------|
| **PC-1** | 모든 파라미터는 `parameter_catalog.yaml`에 정의 필수 (id, family, type, unit, valid_range) |
| **PC-2** | `ParameterCatalog.resolve()`가 유일한 파라미터 조회 경로 — 우회 금지 |
| **PC-3** | catalog 미등록 파라미터가 L3에 도달하면 **컴파일 거부** (silent pass 금지) |
| **PC-4** | 파라미터 기본값은 catalog에만 정의 — 코드 내 인라인 기본값 금지 |
| **PC-5** | alias 해석은 catalog의 `aliases` 필드로만 — 코드 내 alias 매핑 금지 |
| **PC-6** | 새 도메인 추가 시 `validate_knowledge_pack()`으로 catalog 완전성 검증 |

**CatalogEntry 필수 필드**:
```yaml
# parameter_catalog.yaml 엔트리 예시
- id: max_duty_hours
  family: duty                    # 파라미터 그룹
  semantic_role: constraint_param # constraint_param | objective_param | set_definition
  type: numeric                   # numeric | categorical | boolean | temporal
  unit: hours
  valid_range: [4, 16]            # L3 range 검증에 사용
  aliases: ["최대근무시간", "max_working_hours"]
  default_alias: max_duty_hours
  related_constraints: ["max_duty_duration"]
```

---

#### GR-3. Policy Engine Plugin화 — 도메인별 규칙 플러그인

Policy Engine은 도메인 무관한 범용 실행기이다. 도메인 규칙은 YAML로만 주입한다.

**플러그인 구조**:
```
PolicyEngine (범용 실행기)
  ├── load_policies(domain)       ← knowledge/{domain}/policies.yaml 로딩
  ├── resolve(params, data)       ← 도메인 무관 실행
  ├── generate_canonical_fields() ← derived field 생성
  └── inverse_display()           ← 역변환 (표시용)

policies.yaml (도메인별 플러그인)
  ├── temporal_types:             ← 시간 타입 정의
  ├── derived_fields:             ← 파생 필드 규칙
  ├── activation_conditions:      ← 조건부 활성화 (선언적)
  └── version: "1.0.0"           ← 버전 필수
```

**규칙**:
| 규칙 | 설명 |
|------|------|
| **PE-1** | PolicyEngine 코드에 도메인 특화 로직 금지 — `if domain == "X":` 하드코딩 불가 |
| **PE-2** | `eval()`, `exec()` 절대 금지 — 선언적 activation만 (`field_exists`, `value_in_range` 등) |
| **PE-3** | 새 temporal type 추가 = `policies.yaml`에 선언만으로 가능해야 함 |
| **PE-4** | derived field 로직이 YAML로 표현 불가능하면 → **범용 연산자를 엔진에 추가** (도메인 로직 아님) |
| **PE-5** | 정책 변경 시 `version` bump 필수 — 이전 버전과 결과 비교 가능 |
| **PE-6** | activation_condition은 **파라미터 존재/범위/타입만 참조** — 외부 API 호출 금지 |

**범용 연산자 확장 예시** (PE-4):
```yaml
# 도메인 로직이 아닌, 범용 시간 연산 → 엔진에 추가 OK
derived_fields:
  - name: trip_duration_minute
    operator: subtract          # 범용 연산자
    operands: [trip_arr_abs_minute, trip_dep_abs_minute]
```

---

#### GR-4. Canonical Model Strictness — L3 진입 엄격 검증

Canonical Model(L3)은 전체 파이프라인의 품질 관문이다. 여기를 통과한 데이터만이 컴파일러에 도달한다.

**L3 진입 조건 (모든 항목 AND)**:
```
✅ catalog 등록 파라미터인가?     → PC-3 (미등록 → 거부)
✅ type이 catalog 정의와 일치?    → numeric/categorical/boolean/temporal
✅ valid_range 내인가?            → range 위반 → Gate 차단
✅ provenance가 기록되어 있는가?  → 출처 없는 값 → 거부
✅ confirmed 상태인가?            → hypothesis/suggestion 상태 → 거부
```

**엄격도 레벨**:
| 레벨 | 동작 | 사용 시점 |
|------|------|----------|
| `strict` | 위반 시 즉시 차단 + 진단 리포트 | **운영 (기본값)** |
| `warn` | 위반 시 경고 로깅 + 계속 진행 | 디버깅 |
| `permissive` | 검증 스킵 | 개발 초기 실험용만 |

**진단 리포트 예시**:
```json
{
  "gate": "L3_canonical",
  "status": "BLOCKED",
  "violations": [
    {"param": "max_duty_hours", "issue": "range_violation", "value": 25, "valid_range": [4, 16]},
    {"param": "unknown_param_x", "issue": "catalog_miss", "suggestion": "max_duty_duration?"}
  ],
  "action": "사용자에게 수정 요청"
}
```

**규칙**:
| 규칙 | 설명 |
|------|------|
| **CM-1** | L3에 진입하는 모든 값은 5가지 진입 조건을 **전수 검증** |
| **CM-2** | 검증 실패 시 **silent pass 금지** — 반드시 차단 또는 경고 |
| **CM-3** | heuristic/similarity 결과는 L3 진입 불가 — L2에서 사용자 확인 후만 |
| **CM-4** | 운영 환경은 반드시 `strict` 모드 — `permissive`는 로컬 개발만 |
| **CM-5** | 차단 시 원인 + 수정 가이드를 사용자에게 제공 (진단 리포트) |

---

### 11.4 새 도메인 온보딩 체크리스트

새 도메인을 추가할 때 반드시 확인해야 하는 항목:

```
□ 1. Knowledge Pack 완전성
  □ _index.yaml (도메인 이름, 설명, version, detection_keywords)
  □ constraints.yaml (제약조건, version 필수)
  □ parameter_catalog.yaml (모든 파라미터, version 필수)
  □ policies.yaml (temporal types, derived fields, version 필수)
  □ ambiguity_rules.yaml (모호성 규칙)
  □ domain_aliases.yaml에 별칭 등록

□ 2. validate_knowledge_pack() 통과
  □ 필수 파일 존재 확인
  □ version 필드 존재 확인
  □ constraints ↔ parameter_catalog 교차 참조 정합성

□ 3. Domain Isolation 검증
  □ core/, engine/에 도메인명 하드코딩 없음
  □ domains/{name}/은 core/platform, domains/common만 import
  □ 도메인 간 직접 import 없음 (crew ↛ logistics)

□ 4. Canonical Model 검증
  □ 모든 파라미터가 catalog에 등록
  □ type/range 정의 완전
  □ strict 모드에서 E2E 테스트 통과

□ 5. 테스트
  □ Knowledge Pack 로딩 테스트
  □ Parameter Catalog resolve 테스트
  □ Policy Engine derived field 테스트
  □ Ambiguity Detection 테스트
  □ E2E 파이프라인 테스트 (upload → solve)
```

### 11.5 현재 Guardrail 적용 상태

| Guardrail | 현재 상태 | 잔여 과제 |
|-----------|----------|----------|
| GR-1 Domain Isolation | ✅ core/engine에 도메인 import 없음 | CI 자동 검증 스크립트 추가 필요 |
| GR-2 Parameter Catalog | △ 일부 파라미터 코드 내 하드코딩 잔존 | data_binder.py:636-654 alias 마이그레이션 |
| GR-3 Policy Engine Plugin | ✅ eval 미사용, YAML 선언적 | 범용 연산자 확장 (새 도메인 대비) |
| GR-4 Canonical Strictness | △ strict 모드 부분 적용 | 전수 검증 + 진단 리포트 고도화 |

---

## 12. 상용 운영 아키텍처

### 12.1 에러 복구 & 회복탄력성 (Resilience)

상용 플랫폼은 외부 의존성(LLM, 솔버, DB) 장애 시에도 사용자 경험을 유지해야 한다.

#### 장애 유형별 복구 전략

| 장애 유형 | 탐지 방법 | 복구 전략 | 사용자 경험 |
|-----------|----------|----------|------------|
| **LLM (Gemini) 타임아웃** | 요청 10초 초과 | Fast-Path fallback → 캐시 응답 | "AI 분석 지연 중, 기본 분류로 진행합니다" |
| **LLM 응답 품질 저하** | confidence < threshold | 사용자 재확인 요청 | "정확한 의도를 파악하지 못했습니다. 선택해주세요" |
| **LLM 할당량 초과** | 429 응답 | 요청 큐잉 + 지수 백오프 | "잠시 후 다시 처리됩니다" |
| **솔버 타임아웃** | Job 실행 시간 초과 | 자동 취소 + 중간 결과 반환 (가능 시) | "시간 초과 — 문제 규모 축소를 제안합니다" |
| **솔버 INFEASIBLE** | 솔버 상태 코드 | 원인 분석 + 완화 제약 제안 | 진단 리포트 + "이 제약을 완화하시겠습니까?" |
| **DB 연결 끊김** | 커넥션 풀 health check | 자동 재연결 + 재시도 (최대 3회) | 투명 복구 (사용자 인지 안 함) |
| **외부 API (D-Wave) 장애** | HTTP 5xx / 타임아웃 | classical 솔버 fallback 제안 | "양자 솔버 연결 불가 — 고전 솔버로 전환하시겠습니까?" |

#### 핵심 패턴

**Circuit Breaker** (외부 서비스 보호):
```
상태: CLOSED → [연속 N회 실패] → OPEN → [대기 T초] → HALF-OPEN → [성공] → CLOSED
                                    ↓ (OPEN 상태)
                               즉시 fallback 응답 (LLM 미호출)
```

**Graceful Degradation 우선순위**:
```
1. 핵심 기능 유지 (데이터 업로드, 저장, 조회) — 절대 중단 불가
2. 솔버 실행 — 비동기 Job으로 격리, 장애가 다른 기능에 전파되지 않음
3. AI 해석 — LLM 장애 시 Fast-Path/캐시로 대체, 정확도 저하 허용
4. 실시간 분석 — 완전 중단 가능, 이후 재처리
```

**재시도 정책**:
| 대상 | 최대 재시도 | 간격 | 조건 |
|------|-----------|------|------|
| DB 쿼리 | 3회 | 지수 백오프 (1s, 2s, 4s) | 연결 에러만 (로직 에러 재시도 금지) |
| LLM 호출 | 2회 | 지수 백오프 (2s, 4s) | 타임아웃/5xx만 |
| 솔버 실행 | 재시도 없음 | — | 사용자 명시적 재실행만 허용 |
| 외부 API | 2회 | 지수 백오프 (3s, 6s) | 5xx/네트워크만 |

---

### 12.2 멀티테넌시 전략

현재 단일 사용자 모델에서 향후 SaaS 멀티테넌시로 확장하기 위한 설계 원칙.

#### 격리 수준

| 격리 대상 | 현재 | 상용 목표 | 구현 방식 |
|-----------|------|----------|----------|
| **데이터** | user_id 기반 | org_id + user_id | Row-Level Security 또는 스키마 분리 |
| **파일** | `uploads/{project_id}/` | `uploads/{org_id}/{project_id}/` | 경로에 org_id prefix |
| **리소스** | 전역 rate limit | 테넌트별 quota | 테넌트별 rate limit + 솔버 실행 quota |
| **설정** | 전역 YAML | base + tenant override | `knowledge/tenants/{org_id}/overrides.yaml` |
| **솔버 접근** | 전역 API 키 | 테넌트별 API 키 | 테넌트별 Fernet 암호화 키 저장 |
| **Knowledge Pack** | 도메인 단위 공유 | base(공통) + tenant(커스텀) | 2-layer merge: base → tenant override |

#### 테넌트 데이터 모델 (향후)
```
Organization (org_id)
  ├── Users (user_id, org_id, role)
  ├── Projects (project_id, org_id, user_id)
  ├── Settings (org-level overrides)
  └── Usage (quota tracking, billing)
```

#### 격리 원칙
- **쿼리에 항상 org_id 조건 포함** — ORM 레벨 자동 필터 (middleware)
- **테넌트 간 데이터 접근 절대 불가** — 관리자(super_admin)도 명시적 전환 필요
- **삭제 시 연쇄 정리** — org 삭제 → 하위 모든 데이터 + 파일 + 설정 정리
- **현재 단계**: user_id 기반 소유권 검증 유지, org_id 컬럼 추가는 MSA 전환 시 함께 진행

---

### 12.3 성능 예산 (SLA / Latency Budget)

각 Layer와 Gate에 최대 허용 시간을 설정하고, 초과 시 타임아웃 + 경고를 발생시킨다.

#### Layer별 시간 예산

| 구간 | 목표 (P95) | 최대 허용 | 초과 시 대응 |
|------|-----------|----------|------------|
| **API 응답 (동기)** | 1초 | 3초 | 경고 로깅 + 클라이언트 알림 |
| **L1 Intent Classification** | 200ms | 2초 | Fast-Path만 사용 (LLM 스킵) |
| **L2 Semantic Resolution** | 100ms | 500ms | 캐시 히트 우선 |
| **L3 Canonical Validation** | 50ms | 200ms | — |
| **L4 Compilation** | 5초 | 30초 | 비동기 Job 전환 + 진행률 표시 |
| **L5 Solver (classical)** | 30초 | 5분 | 자동 취소 + 규모 축소 제안 |
| **L5 Solver (quantum)** | 60초 | 10분 | 비동기 Job 필수 + 폴링 |
| **Gate 검증 (각)** | 20ms | 100ms | — |

#### 성능 모니터링 지표
```
- p50, p95, p99 latency (Layer별, Gate별)
- 솔버 실행 시간 분포 (classical vs quantum)
- LLM 호출 횟수 / 캐시 히트율
- Gate 차단율 (어느 Gate에서 가장 많이 차단되는가)
- 동시 활성 Job 수
```

#### 리소스 제한
| 리소스 | 기본 제한 | 설정 방식 |
|--------|----------|----------|
| 동시 솔버 Job | 5개/사용자 | 환경변수 `MAX_CONCURRENT_JOBS` |
| 업로드 파일 크기 | 50MB | 환경변수 `MAX_UPLOAD_SIZE` |
| 세션 캐시 | 100개 LRU, TTL 1시간 | `SESSION_CACHE_MAX`, `SESSION_TTL_SEC` |
| API Rate Limit | 엔드포인트별 차등 | `slowapi` 설정 |

---

### 12.4 감사 추적 (Audit Trail)

Observability(기술 디버깅)와 별도로, "누가 언제 무엇을 했는가"를 기록하는 **불변 감사 로그** 체계.

#### Observability vs Audit Trail

| | Observability | Audit Trail |
|---|---|---|
| **목적** | 기술 디버깅, 성능 모니터링 | 규제 준수, 책임 추적 |
| **대상** | Layer 입출력, 에러, 메트릭 | 사용자 행위, 데이터 변경 |
| **수명** | 로그 로테이션 (30일) | **장기 보존 (1년+, 규제별 상이)** |
| **변경** | 삭제/수정 가능 | **불변 (append-only)** |
| **접근** | 개발자, 운영팀 | 감사자, 보안팀, 경영진 |

#### 감사 대상 이벤트

| 카테고리 | 이벤트 | 기록 필드 |
|----------|--------|----------|
| **인증** | 로그인, 로그아웃, 실패, 토큰 갱신 | user_id, ip, timestamp, result |
| **데이터** | 파일 업로드, 삭제, 프로젝트 생성/삭제 | user_id, project_id, action, target |
| **솔버** | 실행 요청, 완료, 취소, 실패 | user_id, job_id, solver, duration, status |
| **설정** | 솔버 설정 변경, 사용자 권한 변경 | user_id, target, before, after |
| **관리** | 사용자 생성/삭제, 역할 변경 | admin_id, target_user, action |

#### 감사 로그 스키마 (향후)
```sql
CREATE TABLE audit.logs (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     UUID NOT NULL,
    org_id      UUID,                    -- 멀티테넌시 대비
    action      VARCHAR(50) NOT NULL,    -- LOGIN, UPLOAD, SOLVE, DELETE, ...
    resource    VARCHAR(100),            -- project:123, job:456, user:789
    detail      JSONB,                   -- 변경 전/후, 추가 컨텍스트
    ip_address  INET,
    user_agent  TEXT
);
-- append-only: DELETE/UPDATE 권한 없음 (super_admin만 TRUNCATE 가능)
```

#### 원칙
- **감사 로그 쓰기 실패가 비즈니스 로직을 차단하지 않음** — 비동기 기록, 실패 시 별도 알림
- **로그 위변조 방지** — append-only 테이블, 주기적 해시 체인 검증 (향후)
- **보존 기간**: 기본 1년, 규제 요구사항에 따라 조정

---

### 12.5 LLM 의존성 관리

Gemini(LLM)는 핵심 의존성이지만 외부 서비스이므로, 장애/비용/품질을 체계적으로 관리한다.

#### LLM 사용 구간

| 구간 | 용도 | 대체 가능성 |
|------|------|-----------|
| L1 Intent Classification | 사용자 의도 분류 | Fast-Path로 대체 가능 (정확도 저하) |
| Math Model Generation | 수학 모델 코드 생성 | 대체 불가 (핵심 기능) |
| Result Analysis | 결과 해석/요약 | 템플릿 기반 대체 가능 |
| Ambiguity Resolution | 모호성 해결 지원 | 규칙 기반 대체 가능 |

#### 프롬프트 버전 관리

프롬프트 템플릿도 코드와 동일한 수준으로 버전 관리한다:

```yaml
# configs/prompts/intent_classification.yaml
version: "2.1.0"
model: gemini-2.0-flash
temperature: 0.1
max_tokens: 500
system_prompt: |
  You are an intent classifier for optimization problems...
few_shot_examples:
  - input: "최대 근무시간을 9시간으로 설정해주세요"
    output: {intent: "set_parameter", confidence: 0.95}
changelog:
  - version: "2.1.0"
    date: 2026-03-19
    change: "few-shot 예시 5개 추가, confidence threshold 0.6→0.7"
```

**규칙**:
| 규칙 | 설명 |
|------|------|
| **LLM-1** | 프롬프트 변경 시 version bump 필수 + A/B 비교 테스트 |
| **LLM-2** | model 변경 (flash→pro 등) 시 반드시 regression test |
| **LLM-3** | 하드코딩 프롬프트 금지 — `configs/prompts/*.yaml`로 외부화 |
| **LLM-4** | temperature, max_tokens 등 하이퍼파라미터도 YAML 관리 |

#### 비용/토큰 모니터링

| 지표 | 추적 방법 | 알림 조건 |
|------|----------|----------|
| 일일 토큰 사용량 | LLM 호출 시 input/output 토큰 수 기록 | 일일 예산 80% 초과 |
| 호출당 평균 비용 | 모델별 단가 × 토큰 수 | 호출당 비용 임계치 초과 |
| 캐시 히트율 | 동일 패턴 요청 캐시 적중 비율 | 히트율 50% 미만 (캐시 효과 낮음) |
| 실패율 | 4xx/5xx 응답 비율 | 5% 초과 시 알림 |

#### 캐싱 전략
```
요청 → 정규화(normalize) → 해시 → 캐시 조회
  ├── HIT → 캐시 응답 반환 (LLM 미호출, 비용 0)
  └── MISS → LLM 호출 → 응답 캐시 저장 → 반환
```
- **캐시 키**: intent 분류는 `hash(normalized_text + domain)`, 모델 생성은 캐시 불가
- **캐시 TTL**: intent 24시간, analysis 1시간
- **캐시 무효화**: 프롬프트 version 변경 시 전체 캐시 flush

---

### 12.6 배포 & 환경 전략

#### 환경 분리

| 환경 | 목적 | DB | LLM | 솔버 | 접근 |
|------|------|---|-----|------|------|
| **dev** | 개발/실험 | 로컬 Docker | mock 또는 flash | classical only | 개발자 |
| **staging** | 통합 테스트, QA | 별도 인스턴스 | 실제 (flash) | classical + CQM | QA 팀 |
| **production** | 운영 | 관리형 DB (RDS 등) | 실제 (pro/flash) | 전체 | 사용자 |

#### 환경별 설정 관리
```
.env.dev        → 로컬 개발
.env.staging    → staging 배포
.env.production → 운영 (시크릿 매니저 연동)
```
- **설정 우선순위**: 환경변수 > `.env.{환경}` > `.env` > 코드 기본값
- **시크릿**: production은 `.env` 파일 사용 금지 → AWS Secrets Manager / Vault 등
- **YAML 지식팩**: 환경 간 동일 (버전 고정) — 환경별 차이는 환경변수만

#### CI/CD 파이프라인

```
PR 생성
  → Guardrail 검증 (GR-1 import 체크)
  → 단위 테스트 (pytest)
  → 린트/타입 체크
  → 빌드 (frontend + backend)
  ↓
PR 머지 (main)
  → staging 자동 배포
  → 통합 테스트 (E2E)
  → 성능 테스트 (선택)
  ↓
릴리스 태그
  → production 배포 (승인 필요)
  → smoke test
  → 모니터링 강화 (30분)
  → 이상 감지 시 자동 롤백
```

#### 배포 전략
| 전략 | 용도 | 방식 |
|------|------|------|
| **롤링 배포** | 일반 업데이트 | health check 기반 순차 교체 |
| **Blue-Green** | 메이저 변경 | 전환 + 즉시 롤백 가능 |
| **Canary** | 고위험 변경 | 10% 트래픽 → 모니터링 → 전체 전환 |

#### 무중단 배포 조건
- API 버전 관리 (`/api/v1/`) — breaking change 시 v2 신설, v1 유지
- DB 마이그레이션은 **backward compatible만** 먼저 적용 → 코드 배포 → 이전 버전 정리
- YAML 지식팩 변경은 **무중단** (동적 로딩, 서버 재시작 불필요)

---

## 13. D-Wave 솔버 현황

| 솔버 | Compiler | Executor | 비고 |
|------|----------|----------|------|
| CQM | ✅ | ✅ | 100K constraints |
| BQM | △ (basic) | ✅ | bug fixed |
| NL/Stride | ✅ | ✅ | 전략적 집중, 2M vars |
| Advantage QPU | ✅ (BQM) | pending | direct QPU |
| Advantage2 QPU | ✅ (BQM) | config ✅ | Zephyr topology |

---

## 14. MSA 전환 고려사항

현재 모놀리식 구조이나, 향후 MSA 전환 예정:
- **서비스 경계 의식**: core/, engine/, domains/ 간 의존성 최소화
- **인터페이스 기반 설계**: 서비스 간 통신은 명확한 API/이벤트 기반
- **DB 스키마 분리**: 이미 core/model/job/data/chat/domain/engine 분리
- **설정 외부화**: YAML + 환경변수로 서비스별 독립 설정 가능
- **Stateless 지향**: 세션은 DB/캐시 기반, 서버 인스턴스 무상태
