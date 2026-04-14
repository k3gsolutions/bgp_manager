"""Middleware: regista pedidos HTTP autenticados no ficheiro de auditoria."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..audit_log import log_http_audit
from ..services.jwt_tokens import audit_claims_from_authorization_header


class UserAuditMiddleware(BaseHTTPMiddleware):
    """Não regista corpo de pedidos (evita passwords); query string incluída."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not str(request.url.path).startswith("/api"):
            return await call_next(request)

        path = request.url.path
        skip_http_audit = path.rstrip("/") == "/api/auth/login"

        t0 = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        if skip_http_audit:
            return response

        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        uid, uname, role = audit_claims_from_authorization_header(auth)
        client = request.client.host if request.client else None

        log_http_audit(
            user_id=uid,
            username=uname,
            role=role,
            method=request.method,
            path=path,
            query=request.url.query or None,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client,
        )
        return response
