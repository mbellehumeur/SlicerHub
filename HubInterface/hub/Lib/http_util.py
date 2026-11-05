"""Shared HTTP/auth helpers used by route modules."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse, Response
from oauth_tokens import (
    HUB_AUTH_MODE,
    TokenValidationError,
    admin_auth_required,
    oauth_error_body,
    validate_admin_access,
    validate_request_bearer,
)

from state import hub

_HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
resources_dir = os.path.join(_HUB_DIR, "Resources")


async def _parse_request_body(request: Request) -> dict:
    """Parse request body as JSON or form data, with fallback to raw body parsing."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()
    try:
        form_data = await request.form()
        merged = {}
        for key in form_data.keys():
            values = form_data.getlist(key)
            merged[key] = values[0] if len(values) == 1 else values
        return merged
    except AssertionError as e:
        if "python-multipart" in str(e):
            body = await request.body()
            if body:
                parsed = parse_qs(body.decode())
                return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
            return {}
        raise


def _enforce_bearer_auth(request: Request) -> Dict[str, Any]:
    """When HUB_REQUIRE_AUTH is set, require a valid Bearer JWT."""
    try:
        return validate_request_bearer(request.headers.get("authorization"))
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail=oauth_error_body(str(exc)),
        ) from exc


def _enforce_topic_match(identity: Dict[str, Any], topic: str) -> None:
    """In oidc mode, require the request topic to match the JWT topic claim."""
    if HUB_AUTH_MODE != "oidc":
        return
    if not identity:
        raise HTTPException(
            status_code=401,
            detail=oauth_error_body("Missing authenticated identity"),
        )
    claimed = str(identity.get("topic") or "").strip()
    requested = str(topic or "").strip()
    role = str(identity.get("role") or "").strip()
    if not claimed:
        raise HTTPException(
            status_code=403,
            detail=oauth_error_body("Token missing topic claim"),
        )
    if requested == "*":
        # Service providers may subscribe to all topics; humans may not.
        if role == "service_provider":
            return
        raise HTTPException(
            status_code=403,
            detail=oauth_error_body("Wildcard topic is not allowed in oidc mode"),
        )
    if not requested or requested != claimed:
        raise HTTPException(
            status_code=403,
            detail=oauth_error_body(
                f"Topic {requested!r} does not match token topic {claimed!r}"
            ),
        )


def _enforce_privileged_admin(request: Request) -> Dict[str, Any]:
    """Require admin token and/or Bearer JWT for privileged admin routes."""
    try:
        return validate_admin_access(
            authorization_header=request.headers.get("authorization"),
            admin_token_header=request.headers.get("x-hub-admin-token"),
            query_admin_token=request.query_params.get("admin_token"),
            query_access_token=request.query_params.get("access_token"),
        )
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail=oauth_error_body(str(exc)),
        ) from exc


async def _enforce_privileged_admin_ws(websocket: WebSocket) -> bool:
    """Validate admin WS credentials; close without accept on failure.

    Returns True when the caller may ``accept()`` the socket.
    """
    if not admin_auth_required():
        return True
    try:
        validate_admin_access(
            authorization_header=websocket.headers.get("authorization"),
            admin_token_header=websocket.headers.get("x-hub-admin-token"),
            query_admin_token=websocket.query_params.get("admin_token"),
            query_access_token=websocket.query_params.get("access_token"),
        )
        return True
    except TokenValidationError:
        await websocket.close(code=1008)
        return False


def _subscription_for_admin(sub: Dict) -> Dict:
    """Public subscription view for admin snapshot (omit hub.secret)."""
    view = dict(sub)
    view.pop("secret", None)
    return view


def _bind_endpoint_is_subscribed(endpoint: str) -> bool:
    """True when ``endpoint`` matches an existing websocket subscription id."""
    if not endpoint:
        return False
    for sub in hub.subscriptions:
        if sub.get("websocket_endpoint") == endpoint:
            return True
    return False


def _unsubscribe_endpoint_id(endpoint: Optional[str]) -> Optional[str]:
    """Extract bind UUID from a full URL or return the raw endpoint id."""
    if not endpoint:
        return None
    endpoint = str(endpoint).strip()
    if not endpoint:
        return None
    if "/bind/" in endpoint:
        return endpoint.split("/bind/")[-1].strip() or None
    return endpoint


def _serve_html_page(filename: str):
    """Serve an HTML page from hub/Resources/, fallback to static mount."""
    html_path = os.path.join(resources_dir, filename)
    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return Response(content=html_content, media_type="text/html")
    return RedirectResponse(url=f"/static/{filename}", status_code=302)
