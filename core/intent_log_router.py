"""Intent Log API — Admin 전용 (JWT 인증)

Intent 분류 로그를 조회하여 분류 정확도 모니터링 및 개선에 활용.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
import logging

from .database import get_db
from .models import IntentLogDB, UserDB
from .schemas import IntentLogResponse, IntentLogStats
from .auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intent-logs", tags=["IntentLog"])


@router.get("", response_model=List[IntentLogResponse])
def get_intent_logs(
    project_id: Optional[int] = Query(None, description="프로젝트 ID 필터"),
    skill_name: Optional[str] = Query(None, description="스킬 이름 필터"),
    source: Optional[str] = Query(None, description="분류 소스 필터 (fast_path, llm, fallback, quick_classify)"),
    min_confidence: Optional[float] = Query(None, description="최소 confidence 필터"),
    max_confidence: Optional[float] = Query(None, description="최대 confidence 필터"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _admin: UserDB = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Intent 분류 로그 목록 조회 (최신순)"""
    q = db.query(IntentLogDB)

    if project_id is not None:
        q = q.filter(IntentLogDB.project_id == project_id)
    if skill_name:
        q = q.filter(IntentLogDB.skill_name == skill_name)
    if source:
        q = q.filter(IntentLogDB.source == source)
    if min_confidence is not None:
        q = q.filter(IntentLogDB.confidence >= min_confidence)
    if max_confidence is not None:
        q = q.filter(IntentLogDB.confidence <= max_confidence)

    return q.order_by(IntentLogDB.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats", response_model=IntentLogStats)
def get_intent_log_stats(
    project_id: Optional[int] = Query(None),
    _admin: UserDB = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Intent 분류 통계 요약"""
    q = db.query(IntentLogDB)
    if project_id is not None:
        q = q.filter(IntentLogDB.project_id == project_id)

    total = q.count()

    # source별 분포
    source_rows = (
        q.with_entities(IntentLogDB.source, func.count())
        .group_by(IntentLogDB.source)
        .all()
    )
    by_source = {row[0]: row[1] for row in source_rows}

    # intent별 분포
    intent_rows = (
        q.with_entities(IntentLogDB.intent, func.count())
        .group_by(IntentLogDB.intent)
        .order_by(func.count().desc())
        .limit(20)
        .all()
    )
    by_intent = {row[0]: row[1] for row in intent_rows}

    # low confidence 건수
    low_conf = q.filter(IntentLogDB.confidence < 0.6).count()

    # 평균 confidence
    avg_conf_result = q.with_entities(func.avg(IntentLogDB.confidence)).scalar()
    avg_conf = float(avg_conf_result) if avg_conf_result else 0.0

    return IntentLogStats(
        total=total,
        by_source=by_source,
        by_intent=by_intent,
        low_confidence_count=low_conf,
        avg_confidence=round(avg_conf, 3),
    )


@router.get("/low-confidence", response_model=List[IntentLogResponse])
def get_low_confidence_logs(
    threshold: float = Query(0.6, description="confidence 임계값"),
    limit: int = Query(50, ge=1, le=500),
    _admin: UserDB = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Low confidence 분류 로그 — 개선 대상 메시지 식별용"""
    return (
        db.query(IntentLogDB)
        .filter(IntentLogDB.confidence < threshold)
        .order_by(IntentLogDB.created_at.desc())
        .limit(limit)
        .all()
    )
