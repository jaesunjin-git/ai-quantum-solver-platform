# core/celery_app.py
from celery import Celery

# 1. Celery 앱 생성 (이름: quantum_solver)
celery_app = Celery(
    "quantum_solver",
    broker="redis://localhost:6379/0",  # 메시지 주는 곳 (우체통)
    backend="redis://localhost:6379/0", # 결과 받는 곳
)

# 2. 설정
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    broker_connection_retry_on_startup=True, # ✅ 연결 안정성 옵션 추가 권장
)

# 3. 태스크 모듈 등록 (워커가 찾을 파일 위치)
celery_app.conf.imports = [
    "engine.hybrid_orchestrator" 
]
