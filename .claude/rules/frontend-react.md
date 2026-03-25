---
paths:
  - "frontend/**"
---

# 프론트엔드 작업 규칙

## 기술 스택
- React 19 + TypeScript 5.9 + Vite
- 스타일링: Tailwind CSS (다크 모드 기본: slate-950/900/800)
- 상태 관리: Context API (AuthContext, AppContext)
- API 통신: `authFetch()` 래퍼 (Authorization 헤더 자동 주입)
- 아이콘: lucide-react

## 코딩 규칙
- 컴포넌트: `PascalCase`, 함수형 + `React.FC`
- 함수/변수: `camelCase`, 훅: `useXxx`
- 컴포넌트 300줄 초과 시 분리
- 메모이제이션: `useCallback` (핸들러), `useMemo` (계산값)
- 조건부 클래스: 템플릿 리터럴

## API 통신 패턴
- Vite 프록시: `/api` → `http://localhost:8000`
- async/await + try/catch
- 401 응답 시 로그인 화면 리다이렉트
- Job 폴링: `useJobPolling` 커스텀 훅

## 환경변수
- `frontend/.env`에서 `VITE_` prefix로 관리
- API 타입은 수동 관리 (`types.ts`)
