"""robots.txt, favicon, and icon routes."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from static_spa import _SPA_CLIENT_PATH_PREFIXES

router = APIRouter()

_HUB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RESOURCES_DIR = os.path.join(_HUB_DIR, "Resources")


@router.get("/robots.txt")
async def get_robots_txt():
    """Discourage crawlers from indexing bundled SPA client mounts."""
    lines = ["User-agent: *"]
    for prefix in _SPA_CLIENT_PATH_PREFIXES:
        lines.append(f"Disallow: {prefix}/")
    lines.append("")
    return Response(content="\n".join(lines), media_type="text/plain")

@router.get("/images/3DSlicer-DesktopIcon.png")
async def get_slicer_icon():
    """Serve the desktop icon path expected by existing HTML pages."""
    icon_dir = os.path.join(
        os.path.dirname(_HUB_DIR), "Resources", "Icons"
    )
    logo_path = os.path.join(icon_dir, "HubInterfaceLogo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(_RESOURCES_DIR, "images", "3DSlicer-DesktopIcon.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Icon not found")


@router.get("/favicon.ico")
async def get_favicon(theme: Optional[str] = None):
    """Serve favicon; default 3D Slicer icon (?theme=volview for VolView)."""
    use_volview = (theme or "").strip().lower() == "volview"
    if not use_volview:
        slicer_path = os.path.join(
            _RESOURCES_DIR, "images", "3DSlicer-DesktopIcon.png"
        )
        if os.path.exists(slicer_path):
            return FileResponse(slicer_path, media_type="image/png")
    logo_path = os.path.join(_RESOURCES_DIR, "images", "volview-logo.svg")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")
