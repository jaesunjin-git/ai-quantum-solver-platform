# core/version/version_router.py — Version Management API
from fastapi import APIRouter, HTTPException

from core.version.dataset_service import get_dataset_versions
from core.version.model_service import get_model_versions, get_model_version
from core.version.run_service import get_run_results, get_run_result

router = APIRouter(prefix="/api/projects", tags=["Versions"])


@router.get("/{project_id}/versions/datasets")
def list_dataset_versions(project_id: int):
    return get_dataset_versions(project_id)


@router.get("/{project_id}/versions/models")
def list_model_versions(project_id: int):
    return get_model_versions(project_id)


@router.get("/{project_id}/versions/models/{model_version_id}")
def detail_model_version(project_id: int, model_version_id: int):
    result = get_model_version(model_version_id)
    if not result:
        raise HTTPException(status_code=404, detail="Model version not found")
    return result


@router.get("/{project_id}/versions/runs")
def list_run_results(project_id: int, model_version_id: int = None):
    return get_run_results(project_id, model_version_id)


@router.get("/{project_id}/versions/runs/{run_id}")
def detail_run_result(project_id: int, run_id: int):
    result = get_run_result(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run result not found")
    return result
