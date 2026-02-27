# core/version/run_service.py
import json
import logging
from typing import Dict, List, Optional

from core.database import SessionLocal
from core.models import RunResultDB

logger = logging.getLogger(__name__)


def create_run_result(
    project_id: int,
    model_version_id: int = None,
    domain_type: str = None,
    solver_id: str = "",
    solver_name: str = None,
    solver_params: Dict = None,
    status: str = None,
    objective_value: float = None,
    result_json: Dict = None,
    compile_time_sec: float = None,
    execute_time_sec: float = None,
) -> RunResultDB:
    """새 실행 결과 저장"""
    db = SessionLocal()
    try:
        row = RunResultDB(
            project_id=project_id,
            model_version_id=model_version_id,
            domain_type=domain_type,
            solver_id=solver_id,
            solver_name=solver_name,
            solver_params=json.dumps(solver_params, ensure_ascii=False) if solver_params else None,
            status=status,
            objective_value=objective_value,
            result_json=json.dumps(result_json, ensure_ascii=False, default=str) if result_json else None,
            compile_time_sec=compile_time_sec,
            execute_time_sec=execute_time_sec,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(f"Created run result: project={project_id}, solver={solver_id}, id={row.id}")
        return row
    finally:
        db.close()


def get_run_results(project_id: int, model_version_id: int = None) -> List[Dict]:
    """프로젝트의 실행 결과 목록"""
    db = SessionLocal()
    try:
        query = db.query(RunResultDB).filter(RunResultDB.project_id == project_id)
        if model_version_id:
            query = query.filter(RunResultDB.model_version_id == model_version_id)
        rows = query.order_by(RunResultDB.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "model_version_id": r.model_version_id,
                "domain_type": r.domain_type,
                "solver_id": r.solver_id,
                "solver_name": r.solver_name,
                "status": r.status,
                "objective_value": r.objective_value,
                "compile_time_sec": r.compile_time_sec,
                "execute_time_sec": r.execute_time_sec,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_run_result(run_id: int) -> Optional[Dict]:
    """특정 실행 결과 상세 (result_json 포함)"""
    db = SessionLocal()
    try:
        r = db.query(RunResultDB).filter(RunResultDB.id == run_id).first()
        if not r:
            return None
        return {
            "id": r.id,
            "model_version_id": r.model_version_id,
            "domain_type": r.domain_type,
            "solver_id": r.solver_id,
            "solver_name": r.solver_name,
            "solver_params": json.loads(r.solver_params) if r.solver_params else None,
            "status": r.status,
            "objective_value": r.objective_value,
            "result_json": json.loads(r.result_json) if r.result_json else None,
            "compile_time_sec": r.compile_time_sec,
            "execute_time_sec": r.execute_time_sec,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
    finally:
        db.close()
