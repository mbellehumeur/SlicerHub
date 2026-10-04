"""Bearer token validation for Slicer hub (mock HS256 + Keycloak RS256/JWKS)."""

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


class TokenValidationError(Exception):
    """Raised when a bearer token is missing, malformed, or invalid."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


HUB_MOCK_OAUTH = _env_flag("HUB_MOCK_OAUTH", default=True)
HUB_REQUIRE_AUTH = _env_flag("HUB_REQUIRE_AUTH", default=False)
JWT_ALGORITHM = "HS256"
JWT_DEFAULT_SECRET = os.environ.get("HUB_JWT_SECRET", "slicer-hub-dev-secret-change-me")
JWT_ISSUER = os.environ.get("HUB_JWT_ISSUER", "slicer-hub")
JWT_AUDIENCE = os.environ.get("HUB_JWT_AUDIENCE", "hub-clients")
HUB_OIDC_ISSUER = os.environ.get("HUB_OIDC_ISSUER", "").strip().rstrip("/")
HUB_OIDC_AUDIENCE = os.environ.get("HUB_OIDC_AUDIENCE", "").strip()


def _default_jwks_url(issuer: str) -> str:
    return f"{issuer}/protocol/openid-connect/certs"


_raw_jwks_url = os.environ.get("HUB_OIDC_JWKS_URL", "").strip()
HUB_OIDC_JWKS_URL = _raw_jwks_url or (
    _default_jwks_url(HUB_OIDC_ISSUER) if HUB_OIDC_ISSUER else ""
)

_jwk_client: Optional[PyJWKClient] = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        if not HUB_OIDC_JWKS_URL:
            raise TokenValidationError("OIDC JWKS URL not configured")
        _jwk_client = PyJWKClient(
            HUB_OIDC_JWKS_URL,
            cache_keys=True,
            lifespan=3600,
        )
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


def _verify_rs256_jwt(token: str) -> Dict[str, Any]:
    if not HUB_OIDC_ISSUER:
        raise TokenValidationError("OIDC issuer not configured")
    try:
        client = _get_jwk_client()
        signing_key = client.get_signing_key_from_jwt(token)
        options: Dict[str, Any] = {"verify_aud": bool(HUB_OIDC_AUDIENCE)}
        decode_kwargs: Dict[str, Any] = {
            "algorithms": ["RS256", "RS384", "RS512"],
            "issuer": HUB_OIDC_ISSUER,
            "options": options,
        }
        if HUB_OIDC_AUDIENCE:
            decode_kwargs["audience"] = HUB_OIDC_AUDIENCE
        return jwt.decode(token, signing_key.key, **decode_kwargs)
    except TokenValidationError:
        raise
    except Exception as exc:
        raise TokenValidationError(str(exc)) from exc


def verify_bearer_token(token: str) -> Dict[str, Any]:
    """Validate bearer JWT (HS256 mock or RS256 via JWKS)."""
    if not isinstance(token, str) or not token.strip():
        raise TokenValidationError("Missing token")
    token = token.strip()
    header = _parse_jwt_header(token)
    alg = str(header.get("alg") or "")
    if alg == "HS256":
        return verify_hs256_jwt(token)
    if alg in ("RS256", "RS384", "RS512"):
        return _verify_rs256_jwt(token)
    raise TokenValidationError(f"Unsupported JWT algorithm: {alg or '(none)'}")


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    if not authorization_header:
        return ""
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def claims_to_identity(claims: Dict[str, Any]) -> Dict[str, Any]:
    user_name = (
        claims.get("user_name")
        or claims.get("preferred_username")
        or claims.get("sub")
        or ""
    )
    user_name = str(user_name).strip() if user_name is not None else ""
    topic = claims.get("topic") or claims.get("cast_topic") or ""
    topic = str(topic).strip() if topic is not None else ""
    return {"user_name": user_name, "topic": topic, "claims": claims}


def oauth_error_body(detail: str) -> Dict[str, str]:
    return {"error": "invalid_token", "error_description": detail}


def validate_request_bearer(authorization_header: Optional[str]) -> Dict[str, Any]:
    """Return identity dict when HUB_REQUIRE_AUTH is enabled."""
    if not HUB_REQUIRE_AUTH:
        return {}
    token = extract_bearer_token(authorization_header)
    if not token:
        raise TokenValidationError("Missing Authorization Bearer token")
    claims = verify_bearer_token(token)
    return claims_to_identity(claims)
