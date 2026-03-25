# 보안 체크리스트 (모든 작업 적용)

- API 키/시크릿: Fernet 암호화, `.env` 커밋 금지, 로그에 PII 노출 금지
- 사용자 입력: validation 필수 (OWASP Top 10)
- JWT 인증: 필요한 엔드포인트에 빠짐없이 적용
- 프로젝트 소유권: user_id 기반 검증 필수
- SQL: ORM 우선, raw SQL은 성능 사유 시에만 (주석 필수)
- CORS: 필요 이상으로 열지 않음 (wildcard 금지)
- Rate limiting: 주요 엔드포인트 적용 (chat 30/m, upload 10/m, solve 5/m)
