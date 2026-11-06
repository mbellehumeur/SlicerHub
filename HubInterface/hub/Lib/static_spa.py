"""Bundled SPA client mounts and optional hub-root static site."""

from __future__ import annotations

import os
from typing import List, Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from logging_setup import hub_logger

_HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESOURCES_DIR = os.path.join(_HUB_DIR, "Resources")


# Bundled SPAs at subpaths: mount_path (URL) -> folder under hub/
SPA_CLIENTS = [
    ("worklist-client", "worklist-client"),
    ("reporting-client", "reporting-client"),
    ("classroom-client", "classroom-client"),
    ("slicerlive", "slicerlive"),
    ("hub-mirror", "hub-mirror"),
    ("metaquest", "metaquest"),
    ("slim", "slim"),
    ("ohif", "OHIF-client"),
]

# Optional folder under hub/ for a site at ``/`` (e.g. ``website`` after make_zip).
# Prefer ``HUB_WEBSITE_ROOT`` (absolute/relative path to a built dist with index.html).
HUB_ROOT_SPA_FOLDER = os.environ.get("HUB_ROOT_SPA_FOLDER", "").strip()


def _path_is_under(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or a file/dir inside it."""
    try:
        path_abs = os.path.abspath(path)
        root_abs = os.path.abspath(root)
        return os.path.commonpath([path_abs, root_abs]) == root_abs
    except ValueError:
        return False


def _resolve_hub_root_site_dir() -> Optional[str]:
    """Directory for GET ``/`` marketing/static site, or None if disabled."""
    env_root = os.environ.get("HUB_WEBSITE_ROOT", "").strip()
    if env_root:
        path = os.path.abspath(env_root)
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
        hub_logger.warning(
            "HUB_WEBSITE_ROOT set but index.html missing: %s", path
        )
        return None
    bundled = os.path.join(_HUB_DIR, "website")
    if os.path.isfile(os.path.join(bundled, "index.html")):
        return bundled
    folder = HUB_ROOT_SPA_FOLDER
    if not folder:
        return None
    path = folder if os.path.isabs(folder) else os.path.join(_HUB_DIR, folder)
    path = os.path.abspath(path)
    if os.path.isfile(os.path.join(path, "index.html")):
        return path
    return None


# Known crawler / spiderbot User-Agent substrings (matched case-insensitively).
_SPIDER_BOT_UA_MARKERS = frozenset(
    {
        "googlebot",
        "bingbot",
        "slurp",
        "duckduckbot",
        "baiduspider",
        "yandexbot",
        "bytespider",
        "petalbot",
        "ahrefs",
        "semrush",
        "mj12bot",
        "dotbot",
        "gptbot",
        "claudebot",
        "anthropic-ai",
        "ccbot",
        "facebookexternalhit",
        "twitterbot",
    }
)

_SPA_CLIENT_PATH_PREFIXES = tuple(
    f"/{mount.strip('/')}" for mount, _folder in SPA_CLIENTS
)


def _is_spider_bot(user_agent: str) -> bool:
    """True when User-Agent looks like a known search/AI/SEO crawler."""
    ua = (user_agent or "").lower()
    return any(marker in ua for marker in _SPIDER_BOT_UA_MARKERS)


def _is_spa_client_path(path: str) -> bool:
    """True for bundled SPA mounts (e.g. /worklist-client, /slicerlive/...)."""
    for prefix in _SPA_CLIENT_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _register_spa_client(app, mount_path: str, client_folder: str) -> bool:
    """Serve a built SPA at /{mount_path}/ from hub/{client_folder}/."""
    mount_path = mount_path.strip("/")
    client_dir = os.path.join(_HUB_DIR, client_folder)
    index_path = os.path.join(client_dir, "index.html")
    if not os.path.isdir(client_dir) or not os.path.isfile(index_path):
        return False

    assets_dir = os.path.join(client_dir, "assets")
    mount_slug = mount_path.replace("-", "_")
    if os.path.isdir(assets_dir):
        app.mount(
            f"/{mount_path}/assets",
            StaticFiles(directory=assets_dir),
            name=f"spa_{mount_slug}_assets",
        )

    async def spa_root():
        return RedirectResponse(url=f"/{mount_path}/")

    async def spa_files(full_path: str):
        # Legacy vtk-js HubClient example → Integrated Worklist SPA root
        if mount_path == "worklist-client":
            legacy = full_path.rstrip("/")
            if legacy in (
                "examples/HubClient.html",
                "examples/HubClient/index.html",
                "examples/HubClient",
            ) or legacy.startswith("examples/HubClient/"):
                return RedirectResponse(
                    url="/worklist-client/", status_code=302
                )

        requested_file = os.path.normpath(os.path.join(client_dir, full_path))
        if not _path_is_under(requested_file, client_dir):
            raise HTTPException(status_code=404, detail="Not found")
        if os.path.isfile(requested_file):
            return FileResponse(requested_file)
        if full_path == "favicon.ico":
            svg_path = os.path.join(client_dir, "favicon.svg")
            if os.path.isfile(svg_path):
                return FileResponse(svg_path, media_type="image/svg+xml")
            if mount_path == "worklist-client":
                fallback = os.path.join(
                    _RESOURCES_DIR, "images", "hub-radio-favicon.svg"
                )
                if os.path.isfile(fallback):
                    return FileResponse(fallback, media_type="image/svg+xml")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        raise HTTPException(
            status_code=404,
            detail=f"{client_folder}/index.html not found",
        )

    app.add_api_route(f"/{mount_path}", spa_root, methods=["GET"])
    app.add_api_route(
        f"/{mount_path}/{{full_path:path}}",
        spa_files,
        methods=["GET"],
    )
    return True


def _register_hub_root_spa(app, client_dir: str) -> bool:
    """Serve a built static site (index.html + assets) at hub ``/`` (GET)."""
    client_dir = os.path.abspath(str(client_dir or "").strip())
    index_path = os.path.join(client_dir, "index.html")
    if not os.path.isdir(client_dir) or not os.path.isfile(index_path):
        return False

    assets_dir = os.path.join(client_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="spa_hub_root_assets",
        )

    async def hub_root_spa(full_path: str = ""):
        relative = full_path.lstrip("/")
        if relative:
            requested_file = os.path.normpath(os.path.join(client_dir, relative))
            if not _path_is_under(requested_file, client_dir):
                raise HTTPException(status_code=404, detail="Not found")
            if os.path.isfile(requested_file):
                return FileResponse(requested_file)
            # Missing css/js/images/etc. → 404 (do not fall back to index.html).
            _stem, ext = os.path.splitext(relative)
            if ext:
                raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index_path)

    app.add_api_route("/", hub_root_spa, methods=["GET"], name="hub_root_spa_index")
    app.add_api_route(
        "/{full_path:path}",
        hub_root_spa,
        methods=["GET"],
        name="hub_root_spa_catchall",
    )
    return True



# Public aliases (same callables).
path_is_under = _path_is_under
register_spa_client = _register_spa_client
register_hub_root_spa = _register_hub_root_spa
resolve_hub_root_site_dir = _resolve_hub_root_site_dir
