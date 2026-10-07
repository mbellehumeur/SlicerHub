"""Shared hub OAuth presets for resource servers and image display."""

from __future__ import annotations

from typing import Any, Dict

HUBS: Dict[str, Dict[str, Any]] = {
    "SLICER-HUB-CLOUD": {
        "hub_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/api/hub",
        "authorization_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/authorize",
        "token_endpoint": "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/token",
        "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
        "client_secret": "0c931e4163c1bc984b5266735dc652a2f1e3e6e8d8cfe5b0855f433cc8ff018f",
        "lease": 999,
    },
    "SLICER-HUB": {
        "hub_endpoint": "http://127.0.0.1:2018/api/hub",
        "authorization_endpoint": "http://127.0.0.1:2018/oauth/authorize",
        "token_endpoint": "http://127.0.0.1:2018/oauth/token",
        "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
        "client_secret": "0c931e4163c1bc984b5266735dc652a2f1e3e6e8d8cfe5b0855f433cc8ff018f",
        "lease": 999,
    },
}

USER_NAME = "3dslicer-server"
DEFAULT_HUB_NAME = "SLICER-HUB"
