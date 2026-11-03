"""OIDC discovery document fetch/cache (Google, Keycloak, etc.)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("hub.oidc_discovery")

_CACHE: Dict[str, Any] = {"issuer": "", "doc": None, "fetched_at": 0.0}
_CACHE_TTL_SECONDS = 3600.0


class OidcDiscoveryError(Exception):
    """Raised when OpenID discovery fails."""


def _discovery_url(issuer: str) -> str:
    base = issuer.rstrip("/") + "/"
    return urljoin(base, ".well-known/openid-configuration")


def fetch_openid_configuration(issuer: str, *, force: bool = False) -> Dict[str, Any]:
    """Return the OpenID Provider Metadata document for ``issuer``."""
    issuer_key = (issuer or "").strip().rstrip("/")
    if not issuer_key:
        raise OidcDiscoveryError("OIDC issuer is empty")

    now = time.time()
    if (
        not force
        and _CACHE.get("issuer") == issuer_key
        and isinstance(_CACHE.get("doc"), dict)
        and now - float(_CACHE.get("fetched_at") or 0) < _CACHE_TTL_SECONDS
    ):
        return dict(_CACHE["doc"])

    url = _discovery_url(issuer_key)
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=15) as resp:
            import json

            doc = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise OidcDiscoveryError(f"OIDC discovery failed for {url}: {exc}") from exc

    if not isinstance(doc, dict):
        raise OidcDiscoveryError("OIDC discovery document is not an object")
    if not doc.get("authorization_endpoint") or not doc.get("token_endpoint"):
        raise OidcDiscoveryError(
            "OIDC discovery missing authorization_endpoint or token_endpoint"
        )
    if not doc.get("jwks_uri"):
        raise OidcDiscoveryError("OIDC discovery missing jwks_uri")

    _CACHE["issuer"] = issuer_key
    _CACHE["doc"] = doc
    _CACHE["fetched_at"] = now
    LOGGER.info("OIDC discovery loaded issuer=%s jwks_uri=%s", issuer_key, doc.get("jwks_uri"))
    return dict(doc)


def get_cached_openid_configuration(issuer: str) -> Optional[Dict[str, Any]]:
    issuer_key = (issuer or "").strip().rstrip("/")
    if _CACHE.get("issuer") == issuer_key and isinstance(_CACHE.get("doc"), dict):
        return dict(_CACHE["doc"])
    return None
