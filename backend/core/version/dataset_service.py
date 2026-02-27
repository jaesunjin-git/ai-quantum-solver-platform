# core/version/dataset_service.py
import json
import hashlib
import logging
from typing import Dict, List

from core.database import SessionLocal
from core.models import DatasetVersionDB

logger = logging.getLogger(__name__)


def create_dataset_version(
    project_id: int,
    file_list: List[str],
    domain_type: str = None,
    description: str = None,
) -> DatasetVersionDB:
    """새 데이터셋 버전 생성. 동일 파일이면 기존 버전 반환."""
    db = SessionLocal()
    try:
        file_hash = hashlib.md5(json.dumps(sorted(file_list)).encode()).hexdigest()

        existing = (
            db.query(DatasetVersionDB)
            .filter(
                DatasetVersionDB.project_id == project_id,
                DatasetVersionDB.file_hash == file_hash,
            )
            .first()
        )
        if existing:
            logger.info(f"Dataset version exists: project={project_id}, v={existing.version}")
            return existing

        max_ver = (
            db.query(DatasetVersionDB.version)
            .filter(DatasetVersionDB.project_id == project_id)
            .order_by(DatasetVersionDB.version.desc())
            .first()
        )
        next_ver = (max_ver[0] + 1) if max_ver else 1

        row = DatasetVersionDB(
            project_id=project_id,
            version=next_ver,
            domain_type=domain_type,
            file_hash=file_hash,
            file_list=json.dumps(file_list, ensure_ascii=False),
            description=description or f"v{next_ver}",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(f"Created dataset version: project={project_id}, v={next_ver}, id={row.id}")
        return row
    finally:
        db.close()


def get_dataset_versions(project_id: int) -> List[Dict]:
    """프로젝트의 데이터셋 버전 목록"""
    db = SessionLocal()
    try:
        rows = (
            db.query(DatasetVersionDB)
            .filter(DatasetVersionDB.project_id == project_id)
            .order_by(DatasetVersionDB.version.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "version": r.version,
                "domain_type": r.domain_type,
                "file_list": json.loads(r.file_list) if r.file_list else [],
                "file_hash": r.file_hash,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()
