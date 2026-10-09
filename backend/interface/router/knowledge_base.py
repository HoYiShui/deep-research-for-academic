"""Canonical, owner-scoped knowledge management; no legacy writable registry."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from application.bootstrap import get_container
from application.errors import AppError
from application.knowledge_models import KnowledgeBaseCreate, KnowledgeBasePatch
from interface.deps import require_user
from interface.router.research import request_key

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-base"])


def service(request):
    value = getattr(get_container(request), "knowledge_management", None)
    if value is None:
        raise AppError("service_not_ready", "Knowledge management is not ready", retryable=True)
    return value


async def empty_body(request):
    async for chunk in request.stream():
        if chunk:
            raise AppError("validation_error", "Deletion does not accept a body")


@router.post("", status_code=201)
async def create_kb(
    body: KnowledgeBaseCreate,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    status, result = await service(request).create(UUID(user), body, key)
    return JSONResponse(status_code=status, content=result)


@router.get("")
async def list_kbs(
    request: Request,
    user: str = Depends(require_user),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=1024),
    status: Literal["creating", "active", "deleting", "deleted"] | None = None,
):
    return await service(request).list(UUID(user), limit=limit, cursor=cursor, status=status)


@router.get("/{kb_id}")
async def get_kb(kb_id: UUID, request: Request, user: str = Depends(require_user)):
    return await service(request).get(UUID(user), kb_id)


@router.patch("/{kb_id}")
async def update_kb(
    kb_id: UUID,
    body: KnowledgeBasePatch,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    status, result = await service(request).update(UUID(user), kb_id, body, key)
    return JSONResponse(status_code=status, content=result)


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: UUID,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    await empty_body(request)
    status, result = await service(request).delete(UUID(user), kb_id, key)
    return JSONResponse(status_code=status, content=result)


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: UUID,
    request: Request,
    user: str = Depends(require_user),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=1024),
):
    return await service(request).documents(UUID(user), kb_id, limit=limit, cursor=cursor)


@router.get("/{kb_id}/documents/{document_id}")
async def get_document(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: str = Depends(require_user),
):
    return await service(request).document(UUID(user), kb_id, document_id)


@router.get("/{kb_id}/documents/{document_id}/versions")
async def list_versions(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: str = Depends(require_user),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=1024),
):
    return await service(request).documents(
        UUID(user), kb_id, document_id=document_id, limit=limit, cursor=cursor
    )


@router.delete("/{kb_id}/documents/{document_id}")
async def delete_document(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
):
    await empty_body(request)
    status, result = await service(request).delete(UUID(user), kb_id, key, document_id=document_id)
    return JSONResponse(status_code=status, content=result)
