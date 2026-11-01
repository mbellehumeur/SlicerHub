"""Mock OAuth authorize/token endpoints."""

from __future__ import annotations

import os
import secrets
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from oauth_tokens import (
    HUB_MOCK_OAUTH,
    JWT_AUDIENCE,
    JWT_DEFAULT_SECRET,
    JWT_ISSUER,
    TokenValidationError,
    build_hs256_jwt,
    verify_hs256_jwt,
)

from http_util import _parse_request_body
from state import hub

router = APIRouter()


JWT_EXPIRY_SECONDS = int(os.environ.get("HUB_JWT_EXPIRY_SECONDS", "3600"))


def _issue_jwt_pair(
    *,
    token_type: str,
    count: int,
    topic: str,
    user_name: str,
) -> str:
    """Issue signed JWT token."""
    now = int(time.time())
    claims = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + JWT_EXPIRY_SECONDS,
        "jti": str(uuid.uuid4()),
        "typ": token_type,
        "token_counter": count,
        "topic": topic,
        "user_name": user_name,
        "scope": "openid",
    }
    return build_hs256_jwt(claims, JWT_DEFAULT_SECRET)


_AUTH_CODE_TTL_SECONDS = 60


def _resolve_authorize_identity(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve user_name / topic for /oauth/authorize.

    Precedence: id_token (claims) > user_name (asserted) > server-generated.
    Always honours single_user_mode and an explicit body 'topic' override.
    Subscriber names are chosen by clients on subscribe, not OAuth.
    Returns { user_name, topic, count }.
    """
    user_name: Optional[str] = None
    topic: Optional[str] = None

    id_token = request_data.get("id_token")
    if isinstance(id_token, str) and id_token.strip():
        try:
            claims = verify_hs256_jwt(
                id_token.strip(), JWT_DEFAULT_SECRET, allow_expired=True
            )
        except TokenValidationError as exc:
            # Map signature/format failures to OAuth invalid_token (401).
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "error_description": str(exc)},
            ) from exc
        user_name = claims.get("user_name") or None
        topic = claims.get("topic") or None

    asserted_user = request_data.get("user_name")
    if not user_name and isinstance(asserted_user, str) and asserted_user.strip():
        user_name = asserted_user.strip()

    if user_name is None:
        # Server-generated identity (mirrors legacy /oauth/token fallback).
        hub.user_count += 1
        count = hub.user_count
        user_name = f"USER-{count}"
        topic = topic or user_name
    else:
        hub.user_count += 1
        count = hub.user_count
        topic = topic or user_name

    if hub.single_user_mode:
        user_name = "SINGLE-USER"
        topic = "SINGLE-USER"

    body_topic = request_data.get("topic")
    if isinstance(body_topic, str) and body_topic.strip() and not hub.single_user_mode:
        topic = body_topic.strip()

    return {
        "user_name": user_name,
        "topic": topic,
        "count": count,
    }


def _purge_expired_auth_codes() -> None:
    """Drop expired/used codes to keep the in-memory store bounded."""
    now = time.time()
    expired = [
        code for code, info in hub.auth_codes.items()
        if info.get("used") or info.get("expires_at", 0) <= now
    ]
    for code in expired:
        hub.auth_codes.pop(code, None)


@router.post("/oauth/authorize")
async def post_oauth_authorize(request: Request):
    """Mock OAuth 2.0 authorization endpoint.

    Accepts a previously-issued id_token (expired or not), a caller-asserted
    user_name, or nothing (server generates USER-N). Returns a one-time code
    bound to the resolved identity. Not for production.

    Note: real OAuth /authorize is GET + browser redirect. This is a
    programmatic POST + JSON mock used by Hub clients.
    """
    if not HUB_MOCK_OAUTH:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "mock_oauth_disabled",
                "error_description": (
                    "Hub mock OAuth is disabled; use Keycloak OIDC endpoints "
                    "(see cast-keycloak README)."
                ),
            },
        )
    try:
        request_data = await _parse_request_body(request)
    except Exception:
        request_data = {}
    request_data.update(dict(request.query_params))

    identity = _resolve_authorize_identity(request_data)

    _purge_expired_auth_codes()
    code = secrets.token_urlsafe(32)
    hub.auth_codes[code] = {
        "user_name": identity["user_name"],
        "topic": identity["topic"],
        "count": identity["count"],
        "expires_at": time.time() + _AUTH_CODE_TTL_SECONDS,
        "used": False,
    }

    await hub.send_admin_refresh_command()

    return {
        "user_name": identity["user_name"],
        "code": code,
        "expires_in": _AUTH_CODE_TTL_SECONDS,
    }


@router.post("/oauth/token")
async def post_oauth_token(request: Request):
    """OAuth token endpoint. Supports grant_type=authorization_code.

    Redeems a one-time code issued by POST /oauth/authorize and returns
    access_token + id_token bound to the same identity.
    """
    if not HUB_MOCK_OAUTH:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "mock_oauth_disabled",
                "error_description": (
                    "Hub mock OAuth is disabled; use Keycloak OIDC token endpoint."
                ),
            },
        )
    try:
        request_data = await _parse_request_body(request)
    except Exception:
        request_data = {}
    request_data.update(dict(request.query_params))

    grant_type = (request_data.get("grant_type") or "").strip()
    code = (request_data.get("code") or "").strip()

    if grant_type and grant_type != "authorization_code":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_grant_type",
                "error_description": f"grant_type '{grant_type}' is not supported; use authorization_code",
            },
        )
    if not code:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "Missing 'code' (obtain one via POST /oauth/authorize)",
            },
        )

    info = hub.auth_codes.get(code)
    if info is None or info.get("used") or info.get("expires_at", 0) <= time.time():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_grant",
                "error_description": "Authorization code is missing, used, or expired",
            },
        )
    info["used"] = True

    user_name = info["user_name"]
    topic = info["topic"]
    count = info["count"]

    access_token = _issue_jwt_pair(
        token_type="access",
        count=count,
        topic=topic,
        user_name=user_name,
    )
    id_token = _issue_jwt_pair(
        token_type="id",
        count=count,
        topic=topic,
        user_name=user_name,
    )

    hub.upsert_authentication(user_name=user_name, topic=topic)

    response = {
        "token_type": "Bearer",
        "expires_in": JWT_EXPIRY_SECONDS,
        "scope": "openid",
        "topic": topic,
        "id_token": id_token,
        "access_token": access_token,
    }
    await hub.send_admin_refresh_command()
    return response
