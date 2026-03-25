# CLAUDE.md — AI Quantum Solver Platform

> 이 파일은 AI 코딩 에이전트(Claude)가 본 프로젝트에서 작업할 때
> 반드시 준수해야 하는 컨텍스트와 규칙을 정의한다.
> 상세 가이드: docs/architecture.md, docs/coding_conventions.md 참조.

## 프로젝트 개요
- **백엔드**: Python/FastAPI (루트) | **프론트엔드**: React/Vite/TS (`frontend/`)
- **솔버**: OR-Tools CP-SAT (classical), D-Wave CQM/BQM/NL (quantum/hybrid)
- **DB**: PostgreSQL (Docker), 스키마: core, model, job, data, chat, domain, engine
- **LLM**: Google Gemini (intent classification, math model, analysis)
- **인증**: JWT (python-jose + bcrypt)
- **현재 도메인**: Crew Scheduling | **향후**: 전방위 도메인 확장

## 디렉토리 구조
```
core/        — 공통 인프라 (auth, db, errors). ⚠️ domains/ import 금지
engine/      — 도메인 무관 범용 엔진. ⚠️ domains/ import 금지
domains/     — 도메인별 확장 (engine 상속). common/skills/ 공유
knowledge/   — YAML 지식팩 (코드 변경 없이 도메인 확장)
configs/     — YAML 설정 (solvers, pipeline, prompts)
frontend/    — React/Vite/TS
tests/       — pytest 테스트
docs/        — 참조 문서
```

## 절대 규칙 (CRITICAL — 위반 시 즉시 중단)
1. `confirmed_problem`에 추측값/heuristic 결과 삽입 금지
2. `core/`, `engine/`에서 `domains/` import 금지 (단방향: domains → core/engine)
3. 코드 내 도메인 특화 상수 하드코딩 금지 → YAML/config에서 로딩
4. API 키, 시크릿 코드/로그 노출 금지
5. 불명확하면 추측하지 말고 사용자에게 질문

## 작업 프로세스
```
① 계획 수립 → ② 사용자 확인 → ③ 개발 → ④ 테스트 → ⑤ 할일 재정리
```
1. **계획 먼저**: 코드 작성 전 반드시 구현 계획을 세우고 사용자 확인. 임의 수정 금지
2. **WHY 설명**: 코드 변경 전 변경 이유를 먼저 설명
3. **범용 관점만**: 모든 수정은 "범용 플랫폼에서 올바른가?"로 판단. 임시방편/단기 수정 금지
4. **구조적 해결 우선**: LLM 프롬프트 튜닝보다 결정적 로직으로 해결. 프롬프트는 끝없이 깨짐
5. **솔버 실행 확인**: 파일 수정 전 uvicorn --reload 재시작 영향 확인
6. **테스트 필수**: 변경 후 `pytest tests/ -v` 실행하여 검증 완료 후 완료 선언
7. **REFACTOR 시**: 이전 실행 결과(column 수, type 분포, solver status)와 비교하여 동작 보존 확인

## 설계 판단 기준
- "다른 도메인에서도 이 코드가 그대로 동작하는가?" — No면 재설계
- "새로운 유형 추가 시 이 파일을 수정해야 하는가?" — No면 재설계
- "이 값이 바뀌면 코드를 수정해야 하는가?" — Yes면 외부화

## 원칙 충돌 시 우선순위
1. 보안 / 데이터 거버넌스 (사용자 데이터 보호)
2. 정확성 (추측 금지, 사용자 확인)
3. 범용성 / 확장성 (하드코딩 금지)
4. 성능 / SLA
5. 편의성

## 코딩 표준 (요약)
- **Python**: 클래스 `PascalCase` / 함수 `snake_case` / 상수 `UPPER_SNAKE` / DB모델 `*DB`
- **TS/React**: 컴포넌트 `PascalCase` / 함수 `camelCase` / 훅 `useXxx` / 300줄 이하 분리
- **공통**: 파일/함수 단위 한국어 주석 필수 / Git 커밋 `feat:`/`fix:`/`chore:` 접두사
- **에러**: `ErrorCode` enum + `error_response()` / YAML 설정 + 환경변수 (`.env`)

## 상세 규칙 (작업 대상에 따라 자동 로딩)
- `engine/`, `core/` 작업 → `.claude/rules/engine-guardrail.md`
- `domains/`, `knowledge/` 작업 → `.claude/rules/domains-extension.md`
- `frontend/` 작업 → `.claude/rules/frontend-react.md`
- 보안 관련 → `.claude/rules/security.md` (항상 로딩)

## 참조 문서
| 문서 | 내용 |
|------|------|
| [docs/architecture.md](docs/architecture.md) | 5-Layer, Guardrail, Observability, 상용 운영 원칙 |
| [docs/coding_conventions.md](docs/coding_conventions.md) | 코딩 컨벤션 상세 (Python, TS, 테스트, 보안) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 3-Track 진화 로드맵 |
| [docs/constraint_architecture.md](docs/constraint_architecture.md) | 제약조건 아키텍처 |

> 현재 상태/TODO → `memory/` 디렉토리의 가장 최근 파일 참조
