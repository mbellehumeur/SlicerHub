"""Shared hub OAuth presets for service providers and image display.

Mock hub OAuth does not validate client_secret. The value below is a public
placeholder (same as SlicerHub-js-clients); do not commit real IdP secrets.

Under HUB_AUTH_MODE=oidc, service providers use Auth0 Machine-to-Machine
credentials resolved by ``resolve_sp_oauth_credentials`` (env → QSettings →
these placeholders).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

# Public placeholder — not a credential (see module docstring).
_MOCK_CLIENT_SECRET = "client_secret_3d_Slicer"

HUBS: Dict[str, Dict[str, Any]] = {
    # Mock auth cloud hub (Fly bellehumeur-hub / apex).
    "SLICER-HUB-CLOUD": {
        "hub_endpoint": "https://bellehumeur-engineering.com/api/hub",
        "authorization_endpoint": "https://bellehumeur-engineering.com/oauth/authorize",
        "token_endpoint": "https://bellehumeur-engineering.com/oauth/token",
        "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
        "client_secret": _MOCK_CLIENT_SECRET,
        "lease": 999,
    },
    # OIDC cloud hub (Fly bellehumeur-hub-oidc). SP M2M still via resolve_sp_oauth_credentials.
    "SLICER-HUB-CLOUD-OIDC": {
        "hub_endpoint": "https://oidc.bellehumeur-engineering.com/api/hub",
        "authorization_endpoint": "https://oidc.bellehumeur-engineering.com/oauth/authorize",
        "token_endpoint": "https://oidc.bellehumeur-engineering.com/oauth/token",
        "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
        "client_secret": _MOCK_CLIENT_SECRET,
        "lease": 999,
    },
    "SLICER-HUB": {
        "hub_endpoint": "http://127.0.0.1:2018/api/hub",
        "authorization_endpoint": "http://127.0.0.1:2018/oauth/authorize",
        "token_endpoint": "http://127.0.0.1:2018/oauth/token",
        "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
        "client_secret": _MOCK_CLIENT_SECRET,
        "lease": 999,
    },
}

USER_NAME = "3dslicer-server"
DEFAULT_HUB_NAME = "SLICER-HUB"

# Slicer QSettings (group HubInterface) keys for SP M2M credentials.
SETTINGS_GROUP = "HubInterface"
SETTINGS_KEY_SP_CLIENT_ID = "spClientId"
SETTINGS_KEY_SP_CLIENT_SECRET = "spClientSecret"


def resolve_sp_oauth_credentials(
    hub_def: Dict[str, Any],
    *,
    qsettings_client_id: str = "",
    qsettings_client_secret: str = "",
) -> Tuple[str, str]:
    """Resolve SP client_id/secret: env → QSettings → hub preset placeholders.

    Env (optional, headless/CI): ``HUB_SP_CLIENT_ID``, ``HUB_SP_CLIENT_SECRET``.
    """
    env_id = os.environ.get("HUB_SP_CLIENT_ID", "").strip()
    env_secret = os.environ.get("HUB_SP_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        return env_id, env_secret
    qs_id = (qsettings_client_id or "").strip()
    qs_secret = (qsettings_client_secret or "").strip()
    if qs_id and qs_secret:
        return qs_id, qs_secret
    return (
        str(hub_def.get("client_id") or "").strip(),
        str(hub_def.get("client_secret") or "").strip(),
    )


def load_sp_credentials_from_qsettings(
    settings: Any,
) -> Tuple[str, str]:
    """Read SP M2M credentials from a ``qt.QSettings`` instance (or compatible)."""
    if settings is None:
        return "", ""
    try:
        settings.beginGroup(SETTINGS_GROUP)
        try:
            client_id = str(settings.value(SETTINGS_KEY_SP_CLIENT_ID, "") or "").strip()
            client_secret = str(
                settings.value(SETTINGS_KEY_SP_CLIENT_SECRET, "") or ""
            ).strip()
        finally:
            settings.endGroup()
        return client_id, client_secret
    except Exception:
        return "", ""


def save_sp_client_id_to_qsettings(settings: Any, client_id: str) -> None:
    if settings is None:
        return
    settings.beginGroup(SETTINGS_GROUP)
    settings.setValue(SETTINGS_KEY_SP_CLIENT_ID, (client_id or "").strip())
    settings.endGroup()


def save_sp_client_secret_to_qsettings(settings: Any, client_secret: str) -> None:
    if settings is None:
        return
    settings.beginGroup(SETTINGS_GROUP)
    settings.setValue(SETTINGS_KEY_SP_CLIENT_SECRET, (client_secret or "").strip())
    settings.endGroup()
