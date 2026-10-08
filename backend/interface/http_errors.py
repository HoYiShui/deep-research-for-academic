"""Public HTTP failures and request tracing, independent of business adapters."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from application.errors import AppError
from domain.ports import AdapterError

ERROR_STATUSES = {
    "malformed_json": 400,
    "unauthenticated": 401,
    "invalid_credentials": 401,
    "session_not_found": 404,
    "artifact_not_found": 404,
    "knowledge_base_not_found": 404,
    "document_not_found": 404,
    "job_not_found": 404,
    "stale_brief": 409,
    "invalid_session_state": 409,
    "stale_resource": 409,
    "report_not_ready": 409,
    "idempotency_conflict": 409,
    "request_in_progress": 409,
    "document_busy": 409,
    "content_identity_conflict": 409,
    "resource_not_active": 409,
    "resume_not_allowed": 409,
    "privacy_policy_conflict": 409,
    "email_already_registered": 409,
    "name_already_exists": 409,
    "file_too_large": 413,
    "unsupported_media_type": 415,
    "validation_error": 422,
    "unsupported_task_type": 422,
    "invalid_filter": 422,
    "invalid_brief": 422,
    "invalid_state": 422,
    "rate_limited": 429,
    "dependency_unavailable": 503,
    "model_output_invalid": 503,
    "content_unavailable": 503,
    "index_not_ready": 503,
    "service_not_ready": 503,
    "internal_error": 500,
}


class RequestIdMiddleware:
    """Generate IDs without trusting client input or buffering streaming bodies."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def traced_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                message = {**message, "headers": headers + [(b"x-request-id", request_id.encode())]}
            await send(message)

        await self.app(scope, receive, traced_send)


def _response(
    request: Request,
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retryable": retryable,
                "details": details,
            }
        },
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


async def app_error(request: Request, exc: AppError) -> JSONResponse:
    status = ERROR_STATUSES.get(exc.code, 500)
    if status == 500:
        return await unexpected_error(request, exc)
    headers = None
    if exc.code in {"request_in_progress", "rate_limited"}:
        retry_after = (exc.details or {}).get("retry_after_s", 5)
        if type(retry_after) is not int or retry_after < 1:
            retry_after = 5
        headers = {"Retry-After": str(retry_after)}
    return _response(
        request,
        status,
        exc.code,
        exc.message,
        retryable=exc.retryable,
        details=exc.details,
        headers=headers,
    )


async def adapter_error(request: Request, exc: AdapterError) -> JSONResponse:
    # Adapter exceptions may include upstream credentials, URLs or document text.
    return _response(
        request,
        503,
        "dependency_unavailable",
        "A required dependency is unavailable.",
        retryable=exc.retryable,
    )


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    malformed = any(error["type"] == "json_invalid" for error in exc.errors())
    unsupported_task = any(
        error["type"] == "literal_error" and error["loc"][-1] == "task_type"
        for error in exc.errors()
    )
    # Do not serialize input, ctx or raw validator messages (they can contain secrets).
    fields = [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
    return _response(
        request,
        400 if malformed else 422,
        "malformed_json"
        if malformed
        else "unsupported_task_type"
        if unsupported_task
        else "validation_error",
        "Invalid JSON." if malformed else "Request fields are invalid.",
        details={"fields": fields},
    )


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    codes = {
        400: "malformed_json",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "invalid_session_state",
        413: "file_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limited",
        503: "service_not_ready",
    }
    if exc.status_code >= 500 and exc.status_code != 503:
        return await unexpected_error(request, exc)
    headers = {
        key: value
        for key, value in (exc.headers or {}).items()
        if key.lower() in {"retry-after", "allow", "www-authenticate"}
    }
    return _response(
        request,
        exc.status_code,
        codes.get(exc.status_code, "http_error"),
        "The request could not be completed.",
        headers=headers,
    )


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return _response(request, 500, "internal_error", "An internal error occurred.")


def install_http_errors(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error)
    app.add_exception_handler(AdapterError, adapter_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected_error)
