"""Local hub endpoints and path bootstrap for Testing smokes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

SMOKE_TOPIC = "USER-1"
SMOKE_PRODUCT = "SMOKE"
SMOKE_USER = "smoke-tester"

# HubInterface/ (parents: common -> Python -> Testing -> HubInterface)
HUBINTERFACE_ROOT = Path(__file__).resolve().parents[3]


def bootstrap_hubinterface_paths() -> Path:
    """Put python_client, service_providers, and image_display/lib on sys.path."""
    roots = (
        HUBINTERFACE_ROOT,
        HUBINTERFACE_ROOT / "python_client" / "src",
        HUBINTERFACE_ROOT / "service_providers",
        HUBINTERFACE_ROOT / "image_display" / "lib",
        Path(__file__).resolve().parents[1],  # Testing/Python
    )
    for root in roots:
        text = str(root)
        if root.is_dir() and text not in sys.path:
            sys.path.insert(0, text)
    return HUBINTERFACE_ROOT


def local_hub_preset() -> Dict[str, Any]:
    """Return SLICER-HUB preset dict (local :2018)."""
    bootstrap_hubinterface_paths()
    try:
        from hub_presets import HUBS
    except ImportError:
        return {
            "hub_endpoint": "http://127.0.0.1:2018/api/hub",
            "authorization_endpoint": "http://127.0.0.1:2018/oauth/login",
            "token_endpoint": "http://127.0.0.1:2018/oauth/token",
            "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
            "client_secret": "client_secret_3d_Slicer",
            "lease": 999,
        }
    return dict(HUBS["SLICER-HUB"])


def build_local_hub_config():
    """Build ``hub_client.HubConfig`` for the local mock hub."""
    bootstrap_hubinterface_paths()
    from hub_client import HubConfig

    preset = local_hub_preset()
    return HubConfig(
        hub_endpoint=preset["hub_endpoint"],
        authorization_endpoint=preset["authorization_endpoint"],
        token_endpoint=preset["token_endpoint"],
        client_id=preset["client_id"],
        client_secret=preset["client_secret"],
    )
