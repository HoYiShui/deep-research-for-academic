"""Owner-scoped persistent ingestion job endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from application.bootstrap import get_container
from application.errors import AppError
from interface.deps import require_user
from interface.dto.research import EmptyRequest
from interface.router.research import request_key

router = APIRouter(prefix="/ingestion-jobs", tags=["ingestion"])


def service(request):
    value = getattr(get_container(request), "ingestion", None)
    if value is None:
        raise AppError("service_not_ready", "Ingestion service is not ready", retryable=True)
    return value


@router.get("/{job_id}")
async def get_job(job_id: UUID, request: Request, user: str = Depends(require_user)):
    return await service(request).get(UUID(user), job_id)


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(
    job_id: UUID,
    body: EmptyRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    status, result = await service(request).retry(UUID(user), job_id, key)
    return JSONResponse(status_code=status, content=result)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    body: EmptyRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    status, result = await service(request).cancel(UUID(user), job_id, key)
    return JSONResponse(status_code=status, content=result)
