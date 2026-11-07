"""Mock OAuth + OIDC browser login (Google-ready) endpoints."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from oauth_tokens import (
    HUB_AUTH_MODE,
    HUB_MOCK_OAUTH,
    HUB_OIDC_CLIENT_ID,
    HUB_OIDC_CLIENT_SECRET,
    HUB_OIDC_ISSUER,
    HUB_OIDC_M2M_AUDIENCE,
    HUB_OIDC_REDIRECT_URI,
    HUB_OIDC_SCOPES,
    HUB_REQUIRE_AUTH,
    HUB_SP_CLIENT_IDS,
    JWT_AUDIENCE,
    JWT_DEFAULT_SECRET,
    JWT_ISSUER,
    ROLE_SERVICE_PROVIDER,
    TokenValidationError,
    build_hs256_jwt,
    identity_from_oidc_claims,
    verify_hs256_jwt,
    verify_oidc_id_token,
)
from oidc_discovery import OidcDiscoveryError, fetch_openid_configuration

from http_util import _parse_request_body
from public_url import _request_public_base_url
from state import hub

router = APIRouter()


JWT_EXPIRY_SECONDS = int(os.environ.get("HUB_JWT_EXPIRY_SECONDS", "3600"))
_OIDC_STATE_TTL_SECONDS = 600


def _issue_jwt_pair(
    *,
    token_type: str,
    count: int,
    topic: str,
    user_name: str,
    role: Optional[str] = None,
) -> str:
    """Issue signed JWT token."""
    now = int(time.time())
    claims: Dict[str, Any] = {
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
    if role:
        claims["role"] = role
    return build_hs256_jwt(claims, JWT_DEFAULT_SECRET)


def _resolve_authorize_identity(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve user_name / topic for mock GET /oauth/login.

    Does not bump ``hub.user_count``; ``_mint_hub_session`` owns the counter.
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
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "error_description": str(exc)},
            ) from exc
        user_name = claims.get("user_name") or None
        topic = claims.get("topic") or None

    asserted_user = request_data.get("user_name")
    if not user_name and isinstance(asserted_user, str) and asserted_user.strip():
        user_name = asserted_user.strip()

    next_count = hub.user_count + 1
    if user_name is None:
        user_name = f"USER-{next_count}"
        topic = topic or user_name
    else:
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
        "count": next_count,
    }


def _purge_expired_oidc_states() -> None:
    now = time.time()
    expired = [
        state
        for state, info in hub.oidc_login_states.items()
        if info.get("expires_at", 0) <= now
    ]
    for state in expired:
        hub.oidc_login_states.pop(state, None)


def _mock_oauth_disabled_detail() -> Dict[str, str]:
    return {
        "error": "mock_oauth_disabled",
        "error_description": (
            "Hub mock OAuth is disabled (HUB_AUTH_MODE=oidc or HUB_MOCK_OAUTH=false). "
            "Use GET /oauth/login for browser OIDC login."
        ),
    }


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return (
        __import__("base64")
        .urlsafe_b64encode(digest)
        .rstrip(b"=")
        .decode("ascii")
    )


def _oidc_redirect_uri(request: Request) -> str:
    if HUB_OIDC_REDIRECT_URI:
        return HUB_OIDC_REDIRECT_URI
    base = _request_public_base_url(request).rstrip("/")
    return f"{base}/oauth/callback"


def _allowed_return_origins(request: Request) -> set[str]:
    """Hub origin + HUB_OIDC_RETURN_ORIGINS (comma-separated)."""
    base = _request_public_base_url(request).rstrip("/")
    origins: set[str] = set()
    try:
        parsed = urlparse(base)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}".lower())
    except Exception:
        pass
    raw = os.environ.get("HUB_OIDC_RETURN_ORIGINS", "").strip()
    for part in raw.split(","):
        item = part.strip().rstrip("/")
        if not item:
            continue
        try:
            p = urlparse(item if "://" in item else f"https://{item}")
            if p.scheme and p.netloc:
                origins.add(f"{p.scheme}://{p.netloc}".lower())
        except Exception:
            continue
    return origins


def _is_local_dev_origin(origin: str) -> bool:
    try:
        p = urlparse(origin)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _safe_return_to(raw: str, request: Request) -> str:
    """Resolve post-login redirect. Allows hub origin, configured SPA origins,
    and localhost/127.0.0.1 (any port) for local HubClient SPAs."""
    candidate = (raw or "").strip()
    base = _request_public_base_url(request).rstrip("/")
    if not candidate:
        return f"{base}/worklist-client/"
    if candidate.startswith("/"):
        return f"{base}{candidate}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return f"{base}/worklist-client/"
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return f"{base}/worklist-client/"
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    allowed = _allowed_return_origins(request)
    if origin in allowed or _is_local_dev_origin(origin):
        # Drop any fragment; callback appends its own.
        return candidate.split("#", 1)[0]
    return f"{base}/worklist-client/"


def _mint_hub_session(
    user_name: str,
    topic: str,
    *,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    hub.user_count += 1
    count = hub.user_count
    if hub.single_user_mode and role != ROLE_SERVICE_PROVIDER:
        user_name = "SINGLE-USER"
        topic = "SINGLE-USER"
    access_token = _issue_jwt_pair(
        token_type="access",
        count=count,
        topic=topic,
        user_name=user_name,
        role=role,
    )
    id_token = _issue_jwt_pair(
        token_type="id",
        count=count,
        topic=topic,
        user_name=user_name,
        role=role,
    )
    hub.upsert_authentication(user_name=user_name, topic=topic)
    return {
        "token_type": "Bearer",
        "expires_in": JWT_EXPIRY_SECONDS,
        "scope": "openid",
        "topic": topic,
        "user_name": user_name,
        "id_token": id_token,
        "access_token": access_token,
    }


def _mint_sp_hub_session(client_id: str) -> Dict[str, Any]:
    """Mint hub session JWT for a service provider (topic=*, role=service_provider)."""
    return _mint_hub_session(
        client_id,
        "*",
        role=ROLE_SERVICE_PROVIDER,
    )


async def _token_client_credentials(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Exchange Auth0 M2M client_credentials for a hub SP session JWT."""
    client_id = str(request_data.get("client_id") or "").strip()
    client_secret = str(request_data.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "client_id and client_secret are required",
            },
        )
    if HUB_SP_CLIENT_IDS is not None and client_id not in HUB_SP_CLIENT_IDS:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_client",
                "error_description": "client_id is not allowlisted for hub M2M",
            },
        )
    if not HUB_OIDC_M2M_AUDIENCE:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "m2m_not_configured",
                "error_description": (
                    "HUB_OIDC_M2M_AUDIENCE is required for service provider "
                    "client_credentials"
                ),
            },
        )
    if not HUB_OIDC_ISSUER:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oidc_not_configured",
                "error_description": "HUB_OIDC_ISSUER required",
            },
        )
    try:
        doc = fetch_openid_configuration(HUB_OIDC_ISSUER)
    except OidcDiscoveryError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "oidc_discovery_failed", "error_description": str(exc)},
        ) from exc
    token_endpoint = str(doc.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise HTTPException(status_code=502, detail="OIDC token endpoint missing")

    form = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": HUB_OIDC_M2M_AUDIENCE,
        }
    ).encode("utf-8")
    try:
        req = UrlRequest(
            token_endpoint,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(req, timeout=20) as resp:
            raw_body = resp.read().decode("utf-8")
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        if exc.code in (400, 401, 403):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_client",
                    "error_description": body or str(exc.reason),
                },
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={
                "error": "idp_token_failed",
                "error_description": f"IdP client_credentials failed: {exc}",
            },
        ) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "idp_token_failed",
                "error_description": f"IdP client_credentials failed: {exc}",
            },
        ) from exc

    try:
        token_payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "idp_token_failed",
                "error_description": "IdP token response was not JSON",
            },
        ) from exc
    if not str(token_payload.get("access_token") or "").strip():
        raise HTTPException(
            status_code=502,
            detail={
                "error": "idp_token_failed",
                "error_description": "IdP token response missing access_token",
            },
        )

    session = _mint_sp_hub_session(client_id)
    await hub.send_admin_refresh_command()
    return {
        **session,
        "hub_auth": "oidc",
        "role": ROLE_SERVICE_PROVIDER,
    }


@router.get("/api/hub/auth/config")
@router.get("/api/hub/auth/config/")
async def get_auth_config(request: Request):
    """Public auth mode discovery for HubClient SPAs."""
    base = _request_public_base_url(request).rstrip("/")
    payload: Dict[str, Any] = {
        "mode": HUB_AUTH_MODE,
        "require_auth": bool(HUB_REQUIRE_AUTH),
        "login_url": f"{base}/oauth/login",
        "token_url": f"{base}/oauth/token",
    }
    if HUB_AUTH_MODE == "oidc":
        payload["issuer"] = HUB_OIDC_ISSUER
        if HUB_OIDC_M2M_AUDIENCE:
            payload["m2m_audience"] = HUB_OIDC_M2M_AUDIENCE
    return payload


async def _mock_oauth_login(request: Request) -> Dict[str, Any]:
    """Mint hub session tokens as JSON (no redirect / no auth code)."""
    if not HUB_MOCK_OAUTH:
        raise HTTPException(status_code=503, detail=_mock_oauth_disabled_detail())
    request_data = dict(request.query_params)
    identity = _resolve_authorize_identity(request_data)
    session = _mint_hub_session(identity["user_name"], identity["topic"])
    await hub.send_admin_refresh_command()
    return {
        **session,
        "hub_auth": "mock",
    }


@router.get("/oauth/login")
async def oauth_login(
    request: Request,
    return_to: str = "",
    response_mode: str = "",
):
    """Login entrypoint for both auth modes.

    Mock: return hub session tokens as JSON (no browser redirect).
    OIDC: browser redirect to IdP; ``response_mode=query`` puts tokens on
    ``return_to`` as a query string (native loopback). Default is fragment (SPAs).
    """
    if HUB_AUTH_MODE == "mock":
        return await _mock_oauth_login(request)
    if HUB_AUTH_MODE != "oidc":
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oidc_disabled",
                "error_description": "Set HUB_AUTH_MODE=oidc to enable browser login",
            },
        )
    if not HUB_OIDC_ISSUER or not HUB_OIDC_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oidc_not_configured",
                "error_description": "HUB_OIDC_ISSUER and HUB_OIDC_CLIENT_ID required",
            },
        )
    try:
        doc = fetch_openid_configuration(HUB_OIDC_ISSUER)
    except OidcDiscoveryError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "oidc_discovery_failed", "error_description": str(exc)},
        ) from exc

    authorize_endpoint = str(doc.get("authorization_endpoint") or "").strip()
    if not authorize_endpoint:
        raise HTTPException(status_code=502, detail="OIDC authorize endpoint missing")

    mode = (response_mode or "").strip().lower()
    if mode not in ("", "fragment", "query"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_response_mode",
                "error_description": "response_mode must be fragment (default) or query",
            },
        )
    delivery = "query" if mode == "query" else "fragment"

    _purge_expired_oidc_states()
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    hub.oidc_login_states[state] = {
        "code_verifier": code_verifier,
        "return_to": _safe_return_to(return_to, request),
        "response_mode": delivery,
        "expires_at": time.time() + _OIDC_STATE_TTL_SECONDS,
    }

    params = {
        "client_id": HUB_OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": HUB_OIDC_SCOPES,
        "redirect_uri": _oidc_redirect_uri(request),
        "state": state,
        "code_challenge": _pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    # Google-friendly: force account chooser when useful
    if "accounts.google.com" in HUB_OIDC_ISSUER:
        params["access_type"] = "online"
        params["include_granted_scopes"] = "true"

    url = f"{authorize_endpoint}?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Complete OIDC login; redirect to return_to with hub session tokens in the hash."""
    if HUB_AUTH_MODE != "oidc":
        raise HTTPException(status_code=503, detail="OIDC mode is not enabled")
    if error:
        raise HTTPException(
            status_code=401,
            detail={
                "error": error,
                "error_description": error_description or "IdP returned an error",
            },
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    _purge_expired_oidc_states()
    pending = hub.oidc_login_states.pop(state, None)
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC state")

    try:
        doc = fetch_openid_configuration(HUB_OIDC_ISSUER)
    except OidcDiscoveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    token_endpoint = str(doc.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise HTTPException(status_code=502, detail="OIDC token endpoint missing")

    form = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oidc_redirect_uri(request),
            "client_id": HUB_OIDC_CLIENT_ID,
            "client_secret": HUB_OIDC_CLIENT_SECRET,
            "code_verifier": pending["code_verifier"],
        }
    ).encode("utf-8")
    try:
        req = UrlRequest(
            token_endpoint,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(req, timeout=20) as resp:
            token_payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OIDC token exchange failed: {exc}",
        ) from exc

    id_token = str(token_payload.get("id_token") or "").strip()
    if not id_token:
        raise HTTPException(status_code=502, detail="OIDC token response missing id_token")

    try:
        idp_claims = verify_oidc_id_token(id_token)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_id_token", "error_description": str(exc)},
        ) from exc

    identity = identity_from_oidc_claims(idp_claims)
    if not identity.get("topic"):
        raise HTTPException(
            status_code=400,
            detail="OIDC identity missing topic claim (set HUB_OIDC_TOPIC_CLAIM)",
        )

    session = _mint_hub_session(identity["user_name"], identity["topic"])
    await hub.send_admin_refresh_command()

    token_params = urlencode(
        {
            "access_token": session["access_token"],
            "id_token": session["id_token"],
            "token_type": "Bearer",
            "expires_in": str(session["expires_in"]),
            "topic": session["topic"],
            "user_name": session["user_name"],
            "hub_auth": "oidc",
        }
    )
    return_to = str(pending.get("return_to") or "").strip() or _safe_return_to("", request)
    # Strip any existing fragment / query before appending ours.
    return_to = return_to.split("#", 1)[0].split("?", 1)[0]
    delivery = str(pending.get("response_mode") or "fragment").strip().lower()
    if delivery == "query":
        sep = "&" if "?" in return_to else "?"
        return RedirectResponse(url=f"{return_to}{sep}{token_params}", status_code=302)
    return RedirectResponse(url=f"{return_to}#{token_params}", status_code=302)


@router.post("/oauth/token")
async def post_oauth_token(request: Request):
    """OAuth token: oidc client_credentials (SP M2M). Mock humans use GET /oauth/login."""
    try:
        request_data = await _parse_request_body(request)
    except Exception:
        request_data = {}
    request_data.update(dict(request.query_params))

    grant_type = (request_data.get("grant_type") or "").strip()
    if HUB_AUTH_MODE == "oidc" and grant_type == "client_credentials":
        return await _token_client_credentials(request_data)

    raise HTTPException(
        status_code=400,
        detail={
            "error": "unsupported_grant_type",
            "error_description": (
                f"grant_type '{grant_type or '(missing)'}' is not supported; "
                "use client_credentials (oidc) or GET /oauth/login (mock)"
            ),
        },
    )
