"""HTTP middleware for spiderbot filter and request logging."""

from __future__ import annotations

from fastapi import Request, Response

from logging_setup import hub_path_excluded_from_request_log
from state import hub
from static_spa import _is_spa_client_path, _is_spider_bot


async def hub_spiderbot_filter_middleware(request: Request, call_next):
    """Reject known spiderbots on bundled SPA client mounts."""
    if _is_spa_client_path(request.url.path) and _is_spider_bot(
        request.headers.get("user-agent", "")
    ):
        return Response(
            content="Forbidden",
            status_code=403,
            media_type="text/plain",
        )
    return await call_next(request)


async def hub_request_logging_middleware(request: Request, call_next):
    """Log selected hub HTTP requests without buffering response bodies.

    Uses the plain ``@app.middleware("http")`` pattern instead of
    ``BaseHTTPMiddleware``, which tees entire response bodies through
    memory streams and throttles large ``/api/hub/payloads/`` downloads.
    """
    path = request.url.path
    method = request.method

    should_log = False
    if path.startswith("/api/hub") and method in ("GET", "POST", "DELETE"):
        if not hub_path_excluded_from_request_log(path):
            should_log = True
    elif path == "/oauth/token" and method == "POST":
        should_log = True
    elif path in ("/oauth/login", "/oauth/callback") and method == "GET":
        should_log = True

    if not should_log:
        return await call_next(request)

    client_host = request.client.host if request.client else "unknown"
    client_port = request.client.port if request.client else "unknown"
    client_addr = f"{client_host}:{client_port}"
    response = await call_next(request)
    status_code = response.status_code
    log_message = f'INFO:     {client_addr} - "{method} {path} HTTP/1.1" {status_code}'
    hub.log(log_message)
    return response
