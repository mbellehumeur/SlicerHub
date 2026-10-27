"""Shared hub OAuth presets for service providers and image display.

Mock hub OAuth does not validate client_secret. The value below is a public
placeholder (same as SlicerHub-js-clients); do not commit real IdP secrets.
"""

from __future__ import annotations

from typing import Any, Dict

# Public placeholder — not a credential (see module docstring).
_MOCK_CLIENT_SECRET = "client_secret_3d_Slicer"

HUBS: Dict[str, Dict[str, Any]] = {
    "SLICER-HUB-CLOUD": {
        "hub_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/api/hub",
        "authorization_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/authorize",
        "token_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/token",
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
