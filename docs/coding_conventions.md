# 코딩 컨벤션 상세

> CLAUDE.md에서 참조하는 상세 규칙 문서

---

## 1. Python 백엔드

### 네이밍
- 클래스: `PascalCase` (예: `UserDB`, `AffineExprIR`)
- 함수/변수: `snake_case` (예: `create_access_token`)
- 상수: `UPPER_SNAKE_CASE` (예: `SECRET_KEY`, `FILE_NOT_UPLOADED`)
- DB 모델: `*DB` 접미사 (예: `UserDB`, `ProjectDB`, `JobDB`)
- Private: 단일 언더스코어 `_load_keywords`
- Frozen dataclass: IR 타입에 `@dataclass(frozen=True)`

### 임포트 순서
```python
from __future__ import annotations          # 1. future
import logging                              # 2. 표준 라이브러리
from fastapi import Depends                 # 3. 서드파티
from core.database import get_db            # 4. 로컬
```

### 모듈 헤더
```python
"""
module_name.py ──────────────────────────────────────────
모듈 설명 (한국어)
"""
```

### 에러 처리
- `ErrorCode` enum + `error_response()` / `warning_response()` 헬퍼
- 응답 필드: `type`, `text`, `data`, `options`, `error_code`
- 구조화된 예외 계층: `StructuredBuildError` → `StructuredFallbackAllowed`, `StructuredDataError`

### DB (SQLAlchemy 2.0)
- 스키마 한정: `__table_args__ = {"schema": "core"}`
- FK 참조: `ForeignKey("core.projects.id")`
- UTC 타임스탬프: `default=lambda: datetime.datetime.now(datetime.timezone.utc)`

### FastAPI 패턴
- Lifespan: `@asynccontextmanager` (startup/shutdown)
- 인증: `Depends(get_current_user)` — 쿼리 파라미터 인증 사용 금지
- Rate limiting: `slowapi` `@limiter.limit()` (chat 30/m, upload 10/m, solve 5/m)
- CORS: wildcard 금지, 명시적 methods/headers

### 로깅
```python
import logging
logger = logging.getLogger(__name__)
```

### 설정 관리
- YAML: `configs/` (pipeline, intents, solvers, keywords, prompts)
- 환경변수: `core/config.py` Settings 클래스, `.env` 로드
- LLM 프롬프트: `configs/prompts/*.yaml` (version, model, temperature 포함)
- 하드코딩 절대 금지 — 도메인 지식은 YAML 지식팩으로, 프롬프트도 YAML로 외부화

---

## 2. TypeScript/React 프론트엔드

### 네이밍
- 컴포넌트: `PascalCase` (예: `ChatMessageBubble`, `SolverCard`)
- 함수/변수: `camelCase` (예: `toggleCollapse`, `isAuthenticated`)
- 커스텀 훅: `use` 접두사 (예: `useAuth`, `useJobPolling`)
- 상수: `UPPER_SNAKE_CASE` (예: `API_BASE_URL`)

### React 패턴
- 함수형 컴포넌트 + `React.FC` 타입
- 상태: Context API (`createContext` + `useContext`)
- 메모이제이션: `useCallback` (핸들러), `useMemo` (계산값)
- 컴포넌트 분할: 큰 컴포넌트는 300줄 이하로 분리

### 스타일링
- Tailwind CSS 유틸리티 클래스
- 다크 모드 기본 (slate-950, slate-900, slate-800)
- 조건부 클래스: 템플릿 리터럴 `${collapsed ? 'w-16' : 'w-64'}`

### API 통신
- Vite 프록시: `/api` → `http://localhost:8000`
- `authFetch()` 래퍼: Authorization 헤더 자동 주입
- async/await 패턴

### 빌드 도구
- Vite + React 19 + TypeScript 5.9
- ESLint flat config (v9+), PostCSS + Tailwind

---

## 3. 테스트 규칙

- 프레임워크: pytest
- 위치: `tests/test_*.py`
- 구조: 클래스 기반 그룹핑 (`class TestCoerceScalar:`)
- 메서드: `test_descriptive_name`
- 예외 테스트: `pytest.raises()`
- Mock: `unittest.mock.MagicMock`
- **코드 변경 후 반드시 관련 테스트 실행**: `pytest tests/ -v`

---

## 4. Git 커밋 규칙

- 접두사: `feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`
- 한국어 메시지 허용
- 단계 추적: `feat: H1+H2+H3 — CompilePolicy strict/debug`

---

## 5. 보안 규칙

- `.env` 파일 커밋 금지
- API 키: `core/crypto.py` Fernet 암호화 저장
- JWT: 24시간 만료, `SECRET_KEY` 환경변수
- CORS: wildcard origin/method/header 금지
- Rate limiting: 모든 주요 엔드포인트 적용
- SQL injection: SQLAlchemy ORM/parameterized query만 사용
- XSS: React 기본 이스케이프 + 입력 검증

---

## 6. 주석 규칙

- **파일 단위**: 모듈 헤더 docstring 필수 (한국어)
- **함수 단위**: 목적/파라미터/리턴 한국어 주석 필수
- **섹션 구분**: `# ── Section Name ──` 또는 `# ============`
- **WHY 설명**: 코드가 WHAT을, 주석은 WHY를 설명
- **이모지 허용**: `# ⚠️ 주의`, `# 🌟 핵심 로직`
