---
paths:
  - "**/*.py"
---

# Python 백엔드 규칙

## 네이밍
- 클래스: `PascalCase` (예: `UserDB`, `AffineExprIR`)
- 함수/변수: `snake_case` | 상수: `UPPER_SNAKE_CASE`
- DB 모델: `*DB` 접미사 | Private: `_load_keywords`

## 임포트 순서
1. `from __future__ import annotations`
2. 표준 라이브러리
3. 서드파티
4. 로컬 (`core.`, `engine.`, `domains.`)

## 에러 처리
- `ErrorCode` enum + `error_response()` / `warning_response()`
- 구조화된 예외: `StructuredBuildError` → `StructuredFallbackAllowed`, `StructuredDataError`

## DB (SQLAlchemy 2.0)
- 스키마 한정: `__table_args__ = {"schema": "core"}`
- UTC 타임스탬프: `default=lambda: datetime.datetime.now(datetime.timezone.utc)`
- 테이블/컬럼 추가 시: 반드시 사용자 확인 후 진행
- Alembic 미도입 → 스키마 변경 시 migration SQL도 함께 작성

## FastAPI
- 인증: `Depends(get_current_user)` (쿼리 파라미터 인증 금지)
- Rate limiting: `slowapi` `@limiter.limit()`
- CORS: wildcard 금지

## 로깅
```python
import logging
logger = logging.getLogger(__name__)
```

## 테스트
- 변경된 모듈 관련 테스트 먼저: `pytest tests/test_{module}.py -v`
- 전체 확인: `pytest tests/ -v`
- 새 기능: happy path + edge case + error case 최소 3개
- 솔버 변경 시: 기존 결과(column 수, status)와 비교
