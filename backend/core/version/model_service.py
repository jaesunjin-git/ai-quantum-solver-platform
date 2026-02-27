# core/version/model_service.py
import json
import logging
from typing import Dict, List, Optional

from core.database import SessionLocal
from core.models import ModelVersionDB

logger = logging.getLogger(__name__)


def create_model_version(
    project_id: int,
    dataset_version_id: int = None,
    model_json: Dict = None,
    domain_type: str = None,
    objective_type: str = None,
    objective_summary: str = None,
    variable_count: int = None,
    constraint_count: int = None,
    description: str = None,
) -> ModelVersionDB:
    """새 모델 버전 생성"""
    db = SessionLocal()
    try:
        max_ver = (
            db.query(ModelVersionDB.version)
            .filter(ModelVersionDB.project_id == project_id)
            .order_by(ModelVersionDB.version.desc())
            .first()
        )
        next_ver = (max_ver[0] + 1) if max_ver else 1

        row = ModelVersionDB(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            version=next_ver,
            domain_type=domain_type,
            objective_type=objective_type,
            objective_summary=objective_summary,
            model_json=json.dumps(model_json, ensure_ascii=False) if model_json else None,
            variable_count=variable_count,
            constraint_count=constraint_count,
            description=description or f"v{next_ver}",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(f"Created model version: project={project_id}, v={next_ver}, id={row.id}")
        return row
    finally:
        db.close()


def get_model_versions(project_id: int) -> List[Dict]:
    """프로젝트의 모델 버전 목록"""
    db = SessionLocal()
    try:
        rows = (
            db.query(ModelVersionDB)
            .filter(ModelVersionDB.project_id == project_id)
            .order_by(ModelVersionDB.version.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "version": r.version,
                "dataset_version_id": r.dataset_version_id,
                "domain_type": r.domain_type,
                "objective_type": r.objective_type,
                "objective_summary": r.objective_summary,
                "variable_count": r.variable_count,
                "constraint_count": r.constraint_count,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_model_version(model_version_id: int) -> Optional[Dict]:
    """특정 모델 버전 상세 (model_json 포함)"""
    db = SessionLocal()
    try:
        r = db.query(ModelVersionDB).filter(ModelVersionDB.id == model_version_id).first()
        if not r:
            return None
        return {
            "id": r.id,
            "version": r.version,
            "dataset_version_id": r.dataset_version_id,
            "domain_type": r.domain_type,
            "objective_type": r.objective_type,
            "objective_summary": r.objective_summary,
            "model_json": json.loads(r.model_json) if r.model_json else None,
            "variable_count": r.variable_count,
            "constraint_count": r.constraint_count,
            "description": r.description,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
    finally:
        db.close()
