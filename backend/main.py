# ============================================================
# main.py — v2.0
# ============================================================
# 변경 이력
# v1.0 → v2.0:
#   - 프로젝트 CRUD API를 core/project_router.py로 분리
#   - main.py에서 프로젝트 관련 엔드포인트 제거
#   - project_router 등록 추가
# ============================================================

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List
from urllib.parse import unquote
import json

# 모듈 불러오기
from core.database import engine, SessionLocal, get_db, Base
import core.models as models
import core.schemas as schemas
from core.config import settings

# 라우터 불러오기
from core.settings_router import router as settings_router
from chat.router import router as chat_router
from core.version.version_router import router as version_router
from core.project_router import router as project_router

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# 1. FastAPI 앱 선언
app = FastAPI()

# 2. CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 라우터 등록
app.include_router(chat_router)
app.include_router(version_router)       # /api/chat
app.include_router(project_router)    # /api/projects
app.include_router(settings_router)   # /api/settings

@app.on_event("startup")
def startup():
    # =========================================================
    # 1. 스키마(방) 생성
    # =========================================================
    db = SessionLocal()
    try:
        db.execute(text("CREATE SCHEMA IF NOT EXISTS core"))
        db.execute(text("CREATE SCHEMA IF NOT EXISTS domain"))
        db.execute(text("CREATE SCHEMA IF NOT EXISTS chat"))
        db.execute(text("CREATE SCHEMA IF NOT EXISTS engine"))
        db.execute(text("CREATE SCHEMA IF NOT EXISTS job"))
        db.commit()
    except Exception as e:
        print(f"⚠️ Schema creation warning: {e}")
        db.rollback()
    finally:
        db.close()

    # 2. 테이블 생성
    Base.metadata.create_all(bind=engine)

    # =========================================================
    # 3. 기초 데이터 시딩
    # =========================================================
    db = SessionLocal()

    # [1] 메뉴 데이터
    if db.query(models.MenuDB).count() == 0:
        print("🚀 Initializing Menu Data...")
        menus = [
            models.MenuDB(role="user", label="Dashboard", icon_key="dashboard", path="dashboard"),
            models.MenuDB(role="user", label="Crew Scheduling", icon_key="truck", path="crew"),
            models.MenuDB(role="user", label="Logistics Opt", icon_key="truck", path="logistics"),
            models.MenuDB(role="user", label="Portfolio Opt", icon_key="finance", path="finance"),

            models.MenuDB(role="admin", label="Dashboard", icon_key="dashboard", path="dashboard"),
            models.MenuDB(role="admin", label="Crew Scheduling", icon_key="truck", path="crew"),
            models.MenuDB(role="admin", label="Logistics Opt", icon_key="truck", path="logistics"),
            models.MenuDB(role="admin", label="Portfolio Opt", icon_key="finance", path="finance"),
            models.MenuDB(role="admin", label="Admin Settings", icon_key="settings", path="admin"),
        ]
        db.add_all(menus)
        db.commit()

    # [2] 시나리오 데이터
    if db.query(models.ScenarioDB).count() == 0:
        print("🚀 Initializing Scenarios...")
        crew_config = {
            "title": "부산교통공사 승무원 스케줄링",
            "slots": [
                {"key": "line", "question": "대상 호선은 어디인가요?", "options": ["1호선", "2호선", "3호선"]},
                {"key": "day_type", "question": "운행 요일은?", "options": ["평일", "주말"]},
                {"key": "crew_count", "question": "총 승무원 수는 몇 명인가요?"},
            ],
        }
        db.add(models.ScenarioDB(task_key="crew_scheduling", config=crew_config))
        db.commit()

    # [3] 문제 템플릿 데이터
    if db.query(models.ProblemTemplateDB).count() == 0:
        print("🚀 Initializing Problem Templates...")
        crew_template = models.ProblemTemplateDB(
            task_key="crew_scheduling",
            math_model_type="CSP",
            decision_rules={
                "variable_formula": "crew_count * shifts",
                "thresholds": {"pure_quantum": 50, "hybrid": 5000},
            },
            supported_algorithms=["CQM", "SimulatedAnnealing", "LeapHybrid"],
        )
        logistics_template = models.ProblemTemplateDB(
            task_key="logistics_opt",
            math_model_type="VRP",
            decision_rules={
                "variable_formula": "trucks * nodes",
                "thresholds": {"pure_quantum": 20, "hybrid": 2000},
            },
            supported_algorithms=["QAOA", "VQE", "TabuSearch"],
        )
        db.add(crew_template)
        db.add(logistics_template)
        db.commit()

    db.close()


@app.get("/")
def read_root():
    return {"status": "Quantum Backend is Running!"}


# =========================================================
# 📂 공통 API (Menus, Chat History)
# =========================================================

@app.post("/api/menus", response_model=List[schemas.MenuResponse])
def get_my_menus(request: dict, db: Session = Depends(get_db)):
    return db.query(models.MenuDB).filter(models.MenuDB.role == request.get("role")).all()


@app.get("/api/chat/history")
def get_chat_history(
    project_id: int,
    user: str = Query(..., description="User Name"),
    role: str = Query(default="user", description="User Role"),
    db: Session = Depends(get_db),
):
    decoded_user = unquote(user)

    # 프로젝트 소유자 확인
    project = db.query(models.ProjectDB).filter(models.ProjectDB.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    if role != "admin" and project.owner != decoded_user:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

    logs = (
        db.query(models.ChatHistoryDB)
        .filter(models.ChatHistoryDB.project_id == project_id)
        .order_by(models.ChatHistoryDB.id.asc())
        .all()
    )
    result = []
    for log in logs:
        card_data = json.loads(log.card_json) if log.card_json else None
        options_data = json.loads(log.options_json) if log.options_json else None
        result.append({
            "id": log.id,
            "role": log.role,
            "type": log.message_type,
            "text": log.message_text,
            "card_data": card_data,
            "options": options_data,
        })
    return result