"""Bearer token validation for Slicer hub (mock HS256 + OIDC JWKS)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import jwt
from jwt import PyJWKClient

LOGGER = logging.getLogger("hub.oauth_tokens")

JWT_INSECURE_DEFAULT_SECRET = "slicer-hub-dev-secret-change-me"


class TokenValidationError(Exception):
    """Raised when a bearer token is missing, malformed, or invalid."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_auth_mode() -> str:
    raw = os.environ.get("HUB_AUTH_MODE", "").strip().lower()
    if raw == "oidc":
        return "oidc"
    if raw in ("mock", ""):
        return "mock"
    LOGGER.warning("Unknown HUB_AUTH_MODE=%r; using mock", raw)
    return "mock"


HUB_AUTH_MODE = _resolve_auth_mode()
# Mock endpoints only when mode is mock and HUB_MOCK_OAUTH is not explicitly off.
HUB_MOCK_OAUTH = HUB_AUTH_MODE == "mock" and _env_flag("HUB_MOCK_OAUTH", default=True)
# OIDC mode always requires Bearer on hub APIs.
HUB_REQUIRE_AUTH = True if HUB_AUTH_MODE == "oidc" else _env_flag(
    "HUB_REQUIRE_AUTH", default=False
)
HUB_ADMIN_TOKEN = os.environ.get("HUB_ADMIN_TOKEN", "").strip()
JWT_ALGORITHM = "HS256"
JWT_DEFAULT_SECRET = os.environ.get("HUB_JWT_SECRET", JWT_INSECURE_DEFAULT_SECRET)
JWT_ISSUER = os.environ.get("HUB_JWT_ISSUER", "slicer-hub")
JWT_AUDIENCE = os.environ.get("HUB_JWT_AUDIENCE", "hub-clients")

def _normalize_oidc_issuer(raw: str) -> str:
    issuer = (raw or "").strip().rstrip("/")
    if issuer and "://" not in issuer:
        issuer = "https://" + issuer
    return issuer


HUB_OIDC_ISSUER = _normalize_oidc_issuer(os.environ.get("HUB_OIDC_ISSUER", ""))
HUB_OIDC_AUDIENCE = os.environ.get("HUB_OIDC_AUDIENCE", "").strip()
HUB_OIDC_CLIENT_ID = os.environ.get("HUB_OIDC_CLIENT_ID", "").strip()
HUB_OIDC_CLIENT_SECRET = os.environ.get("HUB_OIDC_CLIENT_SECRET", "").strip()
HUB_OIDC_REDIRECT_URI = os.environ.get("HUB_OIDC_REDIRECT_URI", "").strip()
HUB_OIDC_TOPIC_CLAIM = (
    os.environ.get("HUB_OIDC_TOPIC_CLAIM", "email").strip() or "email"
)
HUB_OIDC_SCOPES = (
    os.environ.get("HUB_OIDC_SCOPES", "openid email profile").strip()
    or "openid email profile"
)
# Auth0 API identifier for Machine-to-Machine (service provider) client_credentials.
HUB_OIDC_M2M_AUDIENCE = os.environ.get("HUB_OIDC_M2M_AUDIENCE", "").strip()
_raw_jwks_url = os.environ.get("HUB_OIDC_JWKS_URL", "").strip()

ROLE_SERVICE_PROVIDER = "service_provider"


def _parse_sp_client_ids() -> Optional[set[str]]:
    """Optional allowlist of Auth0 M2M client_ids. None = any IdP-accepted client."""
    raw = os.environ.get("HUB_SP_CLIENT_IDS", "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            LOGGER.warning("HUB_SP_CLIENT_IDS JSON parse failed; ignoring allowlist")
            return None
        if not isinstance(items, list):
            return None
        ids = {str(x).strip() for x in items if str(x).strip()}
        return ids or None
    ids = {part.strip() for part in raw.split(",") if part.strip()}
    return ids or None


HUB_SP_CLIENT_IDS = _parse_sp_client_ids()


def admin_auth_required() -> bool:
    """True when admin/destructive routes must authenticate."""
    return bool(HUB_ADMIN_TOKEN) or HUB_REQUIRE_AUTH


def jwt_secret_is_insecure_default() -> bool:
    return JWT_DEFAULT_SECRET == JWT_INSECURE_DEFAULT_SECRET


def assert_oidc_runtime_config() -> None:
    """Fail fast when oidc mode is missing required settings."""
    if HUB_AUTH_MODE != "oidc":
        return
    missing = []
    if not HUB_OIDC_ISSUER:
        missing.append("HUB_OIDC_ISSUER")
    if not HUB_OIDC_CLIENT_ID:
        missing.append("HUB_OIDC_CLIENT_ID")
    if not HUB_OIDC_CLIENT_SECRET:
        missing.append("HUB_OIDC_CLIENT_SECRET")
    if jwt_secret_is_insecure_default():
        missing.append("HUB_JWT_SECRET (must not use the insecure default)")
    if missing:
        raise RuntimeError(
            "HUB_AUTH_MODE=oidc requires: " + ", ".join(missing)
        )


_jwk_client: Optional[PyJWKClient] = None
_jwk_client_url: str = ""


def _resolve_jwks_url() -> str:
    if _raw_jwks_url:
        return _raw_jwks_url
    if not HUB_OIDC_ISSUER:
        return ""
    try:
        from oidc_discovery import fetch_openid_configuration

        doc = fetch_openid_configuration(HUB_OIDC_ISSUER)
        return str(doc.get("jwks_uri") or "").strip()
    except Exception as exc:
        LOGGER.warning("OIDC discovery for JWKS failed: %s", exc)
        # Keycloak-shaped fallback only when discovery fails.
        return f"{HUB_OIDC_ISSUER}/protocol/openid-connect/certs"


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client, _jwk_client_url
    jwks_url = _resolve_jwks_url()
    if not jwks_url:
        raise TokenValidationError("OIDC JWKS URL not configured")
    if _jwk_client is None or _jwk_client_url != jwks_url:
        _jwk_client = PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=3600,
        )
        _jwk_client_url = jwks_url
    return _jwk_client


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * pad))


def _parse_jwt_header(token: str) -> Dict[str, Any]:
    if token.count(".") < 2:
        raise TokenValidationError("Malformed JWT")
    header_b64 = token.split(".", 1)[0]
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    except Exception as exc:
        raise TokenValidationError("Invalid JWT header") from exc
    if not isinstance(header, dict):
        raise TokenValidationError("Invalid JWT header")
    return header


def build_hs256_jwt(claims: Dict[str, Any], secret: str = JWT_DEFAULT_SECRET) -> str:
    """Build a compact HS256 JWT using only stdlib + signing."""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def verify_hs256_jwt(
    token: str,
    secret: str = JWT_DEFAULT_SECRET,
    *,
    allow_expired: bool = False,
) -> Dict[str, Any]:
    """Validate HS256 signature; return claims."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise TokenValidationError("Malformed JWT")
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(sig_b64)
    except Exception as exc:
        raise TokenValidationError("Invalid signature encoding") from exc
    if not hmac.compare_digest(expected, actual):
        raise TokenValidationError("Signature mismatch")
    try:
        claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise TokenValidationError("Invalid JWT payload") from exc
    if not isinstance(claims, dict):
        raise TokenValidationError("Invalid JWT claims")
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time() and not allow_expired:
        raise TokenValidationError("Token expired")
    return claims


def _issuer_accept_list(issuer: str) -> list[str]:
    """Auth0 uses a trailing slash on ``iss``; Google usually does not."""
    iss = (issuer or "").strip().rstrip("/")
    if not iss:
        return []
    return [iss, f"{iss}/"]


def _verify_rs256_jwt(
    token: str,
    *,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
) -> Dict[str, Any]:
    issuers = _issuer_accept_list(issuer or HUB_OIDC_ISSUER)
    if not issuers:
        raise TokenValidationError("OIDC issuer not configured")
    aud = (audience if audience is not None else HUB_OIDC_AUDIENCE) or ""
    # Google id_tokens use aud=client_id.
    if not aud and HUB_OIDC_CLIENT_ID:
        aud = HUB_OIDC_CLIENT_ID
    try:
        client = _get_jwk_client()
        signing_key = client.get_signing_key_from_jwt(token)
        options: Dict[str, Any] = {"verify_aud": bool(aud)}
        decode_kwargs: Dict[str, Any] = {
            "algorithms": ["RS256", "RS384", "RS512"],
            "issuer": issuers,
            "options": options,
        }
        if aud:
            decode_kwargs["audience"] = aud
        return jwt.decode(token, signing_key.key, **decode_kwargs)
    except TokenValidationError:
        raise
    except Exception as exc:
        raise TokenValidationError(str(exc)) from exc


def verify_oidc_id_token(token: str) -> Dict[str, Any]:
    """Validate an IdP id_token (e.g. Google) via JWKS."""
    return _verify_rs256_jwt(
        token,
        audience=HUB_OIDC_CLIENT_ID or HUB_OIDC_AUDIENCE or None,
        issuer=HUB_OIDC_ISSUER,
    )


def verify_bearer_token(token: str) -> Dict[str, Any]:
    """Validate bearer JWT (hub HS256 session; RS256 IdP only when not oidc-session)."""
    if not isinstance(token, str) or not token.strip():
        raise TokenValidationError("Missing token")
    token = token.strip()
    header = _parse_jwt_header(token)
    alg = str(header.get("alg") or "")
    if alg == "HS256":
        # OIDC mode: only accept hub-minted session tokens (HS256 with configured secret).
        return verify_hs256_jwt(token)
    if alg in ("RS256", "RS384", "RS512"):
        # Direct IdP access tokens are not used for /api/hub in oidc mode (session JWT only).
        if HUB_AUTH_MODE == "oidc":
            raise TokenValidationError(
                "Hub OIDC mode expects a hub session access_token (HS256), not an IdP JWT"
            )
        return _verify_rs256_jwt(token)
    raise TokenValidationError(f"Unsupported JWT algorithm: {alg or '(none)'}")


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    if not authorization_header:
        return ""
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def identity_from_oidc_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Map IdP claims to hub user_name + topic."""
    topic_claim = HUB_OIDC_TOPIC_CLAIM
    topic_raw = claims.get(topic_claim)
    if topic_raw is None or not str(topic_raw).strip():
        topic_raw = claims.get("email") or claims.get("sub") or ""
    topic = str(topic_raw).strip()
    user_name = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("name")
        or claims.get("sub")
        or topic
        or ""
    )
    user_name = str(user_name).strip() if user_name is not None else ""
    return {"user_name": user_name, "topic": topic, "claims": claims}


def claims_to_identity(claims: Dict[str, Any]) -> Dict[str, Any]:
    user_name = (
        claims.get("user_name")
        or claims.get("preferred_username")
        or claims.get("email")
        or claims.get("sub")
        or ""
    )
    user_name = str(user_name).strip() if user_name is not None else ""
    topic = claims.get("topic") or claims.get("cast_topic") or ""
    topic = str(topic).strip() if topic is not None else ""
    if not topic and HUB_AUTH_MODE == "oidc":
        # Hub session JWT should already carry topic; fall back for safety.
        topic = str(claims.get("email") or claims.get("sub") or "").strip()
    identity: Dict[str, Any] = {
        "user_name": user_name,
        "topic": topic,
        "claims": claims,
    }
    role = claims.get("role")
    if role is not None and str(role).strip():
        identity["role"] = str(role).strip()
    return identity


def oauth_error_body(detail: str) -> Dict[str, str]:
    return {"error": "invalid_token", "error_description": detail}


def validate_request_bearer(authorization_header: Optional[str]) -> Dict[str, Any]:
    """Return identity dict when auth is required (oidc or HUB_REQUIRE_AUTH)."""
    if not HUB_REQUIRE_AUTH:
        return {}
    token = extract_bearer_token(authorization_header)
    if not token:
        raise TokenValidationError("Missing Authorization Bearer token")
    claims = verify_bearer_token(token)
    return claims_to_identity(claims)


def _token_matches_admin(candidate: str) -> bool:
    if not HUB_ADMIN_TOKEN or not candidate:
        return False
    return hmac.compare_digest(candidate, HUB_ADMIN_TOKEN)


def validate_admin_access(
    *,
    authorization_header: Optional[str] = None,
    admin_token_header: Optional[str] = None,
    query_admin_token: Optional[str] = None,
    query_access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Authorize privileged admin routes.

    When neither ``HUB_ADMIN_TOKEN`` nor ``HUB_REQUIRE_AUTH`` is set, allow
    (local demo). Otherwise accept a matching admin token (header/query/Bearer)
    or, if ``HUB_REQUIRE_AUTH``, a valid client JWT.
    """
    if not admin_auth_required():
        return {}

    candidates: list[str] = []
    for raw in (
        admin_token_header,
        query_admin_token,
        extract_bearer_token(authorization_header),
        query_access_token,
    ):
        token = (raw or "").strip()
        if token and token not in candidates:
            candidates.append(token)

    if HUB_ADMIN_TOKEN:
        for candidate in candidates:
            if _token_matches_admin(candidate):
                return {"admin": True, "via": "admin_token"}

    if HUB_REQUIRE_AUTH:
        # Prefer Authorization / access_token; also accept candidates from
        # X-Hub-Admin-Token / admin_token when they are hub session JWTs.
        ordered: list[str] = []
        for raw in (
            extract_bearer_token(authorization_header),
            (query_access_token or "").strip(),
            *candidates,
        ):
            token = (raw or "").strip()
            if token and token not in ordered:
                ordered.append(token)
        if not ordered:
            raise TokenValidationError("Missing Authorization Bearer token")
        last_error: Optional[Exception] = None
        for token in ordered:
            if HUB_ADMIN_TOKEN and _token_matches_admin(token):
                return {"admin": True, "via": "admin_token"}
            try:
                claims = verify_bearer_token(token)
                return claims_to_identity(claims)
            except TokenValidationError as exc:
                last_error = exc
                continue
        raise TokenValidationError(
            str(last_error) if last_error else "Invalid or missing admin token"
        )

    raise TokenValidationError("Invalid or missing admin token")
