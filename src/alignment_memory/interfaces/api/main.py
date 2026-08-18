from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHttpException

from alignment_memory.domain import DomainError, EvidenceValidationError
from alignment_memory.interfaces.api.dependencies import AppContainer
from alignment_memory.interfaces.api.errors import ApiError, error_payload
from alignment_memory.interfaces.api.routes import router
from alignment_memory.interfaces.api.security import InternalHmacVerifier, SupabaseJwtVerifier
from alignment_memory.ports import StaleRepositoryStateError
from alignment_memory.settings import Settings, get_settings

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LOGGER = logging.getLogger("alignment_memory.api")


def create_app(
    settings: Settings | None = None,
    *,
    container: AppContainer | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_container = container or AppContainer(app_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await app_container.start()
        try:
            yield
        finally:
            await app_container.close()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.parsed_cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Alignment-Signature",
            "X-Alignment-Timestamp",
            "X-Request-ID",
        ],
    )
    app.state.settings = app_settings
    app.state.container = app_container
    app.state.jwt_verifier = SupabaseJwtVerifier(app_settings)
    app.state.hmac_verifier = InternalHmacVerifier(app_settings)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid4())
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        _LOGGER.info(
            json.dumps(
                {
                    "event": "http_request",
                    "requestId": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "durationMs": round((time.perf_counter() - started) * 1000, 2),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return _error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            ApiError(
                status_code=422,
                code="invalid_request",
                message="Request parameters or body are invalid",
            ),
        )

    @app.exception_handler(StaleRepositoryStateError)
    async def stale_state_handler(
        request: Request,
        _error: StaleRepositoryStateError,
    ) -> JSONResponse:
        return _error_response(
            request,
            ApiError(
                status_code=409,
                code="stale_repository_state",
                message="Worker result was produced from a stale repository head",
                retryable=True,
            ),
        )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
        status_code = 422 if isinstance(error, EvidenceValidationError) else 409
        code = "invalid_result_evidence" if status_code == 422 else "domain_conflict"
        return _error_response(
            request,
            ApiError(
                status_code=status_code,
                code=code,
                message="The request violates an append-only or validation rule",
            ),
        )

    @app.exception_handler(StarletteHttpException)
    async def http_error_handler(
        request: Request,
        error: StarletteHttpException,
    ) -> JSONResponse:
        message = "Route was not found" if error.status_code == 404 else "Request failed"
        return _error_response(
            request,
            ApiError(
                status_code=error.status_code,
                code="route_not_found" if error.status_code == 404 else "http_error",
                message=message,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        _LOGGER.error(
            json.dumps(
                {
                    "event": "unhandled_api_error",
                    "requestId": _request_id(request),
                    "errorType": type(error).__name__,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return _error_response(
            request,
            ApiError(
                status_code=500,
                code="internal_error",
                message="An unexpected error occurred",
                retryable=True,
            ),
        )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "alignment-memory",
            "mode": app_settings.app_mode,
        }

    app.include_router(router)
    return app


def _error_response(request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error_payload(error, _request_id(request)),
    )


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else str(uuid4())
