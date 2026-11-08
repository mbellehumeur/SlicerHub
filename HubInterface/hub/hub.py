#!/usr/bin/env python
"""
Standalone Slicer Hub implementation.
This is a complete, standalone implementation that doesn't depend on WebServer.py.
Uses FastAPI for HTTP server and WebSocket support.

Run with: python hub.py

Requirements:
    pip install fastapi uvicorn

    Optional (for form data support):
    pip install python-multipart

    Note: Form data will work without python-multipart by parsing raw body,
    but python-multipart provides better form data parsing support.

    slicer.util.pip_install('fastapi', 'uvicorn', 'python-multipart', 'gunicorn')

Environment (hub WebSocket keepalive, default off):
    HUB_WS_KEEPALIVE
        Set to 1/true/yes/on to send JSON {"type":"ping"} on subscriber /bind/ sockets.
    HUB_WS_KEEPALIVE_INTERVAL_SECONDS
        Ping interval when keepalive is enabled (default 30).

Environment (uvicorn WebSocket protocol PING/PONG, default on):
    HUB_UVICORN_WS_PING_INTERVAL_SECONDS
        Numeric seconds between uvicorn WS protocol pings (default 20).
        Set to 0 to disable.
    HUB_UVICORN_WS_PING_TIMEOUT_SECONDS
        PONG-wait timeout when uvicorn pings are enabled (defaults to the interval).

Environment (multipart binary payload store):
    HUB_HTTP_PAYLOAD_TTL_SECONDS
        TTL in seconds for stored payloads served via GET
        /api/hub/payloads/{payloadId} (default 300). A background reaper
        evicts expired entries every min(30s, ttl/4).
    HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES
        Soft cap on total in-flight stored payload bytes (default 2 GiB).
        Registrations beyond this cap fan out metadata-only JSON (no
        payloadId; the message is never dropped).
    HUB_HTTP_PAYLOAD_STORE_CHUNK_BYTES
        Max bytes per stored payload chunk (default 4 MiB). Files larger
        than this are split; fan-out uses context.files[].payloadIds[] and
        chunkByteLengths[]. Set to 0 to store each file as a single chunk.

Environment (binary transfer filename policy, default on):
    HUB_FILENAME_POLICY
        Set to off/0/false/no to disable allowlist checks.
    HUB_ALLOWED_EXTENSIONS
        Comma-separated suffixes (e.g. .dcm,.nii.gz). Defaults are documented
        in filename-policy.md.
"""

import sys
import os
import asyncio
from contextlib import asynccontextmanager

# hub.py is run as a script (see module docstring), so sibling imports
# are the right form here.
_base_dir = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_base_dir, ".env"))
except ImportError:
    pass
_repo_root = os.environ.get("HUB_REPO_ROOT", "").strip() or os.path.dirname(_base_dir)
if _repo_root:
    _repo_root = os.path.abspath(_repo_root)
# Prefer vendored python_client next to hub.py (Azure zip), then monorepo layout.
for _hub_client_src in (
    os.path.join(_base_dir, "python_client", "src"),
    os.path.join(_repo_root, "python_client", "src"),
):
    if os.path.isdir(_hub_client_src) and _hub_client_src not in sys.path:
        sys.path.insert(0, _hub_client_src)
        break
_lib_dir = os.path.join(_base_dir, "Lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from oauth_tokens import (
    HUB_ADMIN_TOKEN,
    HUB_AUTH_MODE,
    HUB_MOCK_OAUTH,
    HUB_OIDC_ISSUER,
    HUB_REQUIRE_AUTH,
    assert_oidc_runtime_config,
)

# Try to import FastAPI - install with: pip install fastapi uvicorn
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
except ImportError:
    print("ERROR: FastAPI not installed. Install with: pip install fastapi uvicorn")
    sys.exit(1)

from env import (
    HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES,
    HUB_HTTP_PAYLOAD_TTL_SECONDS,
    HUB_UVICORN_WS_PING_INTERVAL,
    HUB_UVICORN_WS_PING_TIMEOUT,
    HUB_WS_KEEPALIVE,
    HUB_WS_KEEPALIVE_INTERVAL_SECONDS,
    cors_allow_config,
)
from logging_setup import (
    _configure_hub_logging,
    _install_uvicorn_access_log_filter,
    hub_logger,
)
from middleware import (
    hub_request_logging_middleware,
    hub_spiderbot_filter_middleware,
)
from payload_store import _http_payload_reaper
from routes import register_all
from samples import _hub_samples_root, _scan_hub_sample_studies
from state import hub
from static_spa import (
    SPA_CLIENTS,
    register_hub_root_spa,
    register_spa_client,
    resolve_hub_root_site_dir,
)

_configure_hub_logging()

# Filled during module import (SPA mounts below); read by lifespan at startup.
_registered_spa_clients = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup logging / payload reaper; shutdown closes WebSockets."""
    _configure_hub_logging()
    try:
        assert_oidc_runtime_config()
    except RuntimeError as exc:
        hub_logger.error("%s", exc)
        raise
    _install_uvicorn_access_log_filter()
    hub_logger.info("Slicer hub auth mode: %s", HUB_AUTH_MODE)
    hub_logger.info(
        "Slicer hub CORS: origins=%s credentials=%s",
        _cors_origins,
        _cors_credentials,
    )
    if HUB_WS_KEEPALIVE:
        hub_logger.info(
            "Slicer hub WebSocket keepalive: on (interval=%ss, JSON ping/pong)",
            HUB_WS_KEEPALIVE_INTERVAL_SECONDS,
        )
    else:
        hub_logger.info(
            "Slicer hub WebSocket keepalive: off "
            "(set HUB_WS_KEEPALIVE=true to enable)"
        )
    if HUB_UVICORN_WS_PING_INTERVAL is None:
        hub_logger.info(
            "Slicer hub uvicorn WS protocol PING: off "
            "(set HUB_UVICORN_WS_PING_INTERVAL_SECONDS=0 to keep off)"
        )
    else:
        hub_logger.info(
            "Slicer hub uvicorn WS protocol PING: on (interval=%ss, timeout=%ss)",
            HUB_UVICORN_WS_PING_INTERVAL,
            HUB_UVICORN_WS_PING_TIMEOUT,
        )
    hub_logger.info(
        "Slicer hub http payload store: ttl=%ss max_total_bytes=%s",
        HUB_HTTP_PAYLOAD_TTL_SECONDS,
        HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES,
    )
    if _registered_spa_clients:
        hub_logger.info(
            "Slicer hub SPA clients: %s",
            ", ".join(_registered_spa_clients),
        )
    else:
        hub_logger.info(
            "Slicer hub SPA clients: none (build and sync "
            "worklist-client, reporting-client, classroom-client, slicerlive, slim, ohif)"
        )
    sample_count = len(_scan_hub_sample_studies())
    samples_root = _hub_samples_root()
    if sample_count:
        hub_logger.info(
            "Slicer hub samples: %s studies in %s", sample_count, samples_root
        )
    else:
        hub_logger.info(
            "Slicer hub samples: none (%s missing or empty)", samples_root
        )
    reaper_task = asyncio.create_task(_http_payload_reaper())
    try:
        yield
    finally:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        hub_logger.info("Slicer hub shutting down: closing WebSocket connections")
        await hub.reset_all()


app = FastAPI(title="Slicer Hub", lifespan=lifespan)

# Trust reverse-proxy forwarded headers (Azure App Service) before CORS.
_proxy_trusted_hosts = os.environ.get("HUB_PROXY_TRUSTED_HOSTS", "*").strip() or "*"
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_proxy_trusted_hosts)

# CORS: default * without credentials (Bearer clients use credentials:omit).
# Pin with HUB_CORS_ORIGINS=https://a.example,https://b.example to enable credentials.
_cors_origins, _cors_credentials = cors_allow_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Mount static files directory (HTML/CSS/images only — not Lib Python sources)
resources_dir = os.path.join(_base_dir, "Resources")
if os.path.exists(resources_dir):
    app.mount("/static", StaticFiles(directory=resources_dir), name="static")

for _mount_path, _client_folder in SPA_CLIENTS:
    if register_spa_client(app, _mount_path, _client_folder):
        _registered_spa_clients.append(f"/{_mount_path}/")

register_all(app)

# Optional marketing/static site at hub ``/`` (HUB_WEBSITE_ROOT or hub/website/).
# Register after API routers so explicit routes win over the root catch-all.
_hub_root_site_dir = resolve_hub_root_site_dir()
if _hub_root_site_dir and register_hub_root_spa(app, _hub_root_site_dir):
    _registered_spa_clients.append(f"/ ({_hub_root_site_dir})")
else:
    _hub_root_site_dir = None

app.middleware("http")(hub_spiderbot_filter_middleware)
app.middleware("http")(hub_request_logging_middleware)


def main():
    """Main function to run the Slicer Hub server"""
    import argparse

    parser = argparse.ArgumentParser(description="Standalone Slicer Hub Server")
    parser.add_argument("--port", type=int, default=2018, help="Server port (default: 2018)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    args = parser.parse_args()

    try:
        assert_oidc_runtime_config()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    hub.set_server_port(args.port)

    print("=" * 60)
    print("Standalone Slicer Hub Server")
    print("=" * 60)
    print(f"Server running on http://{args.host}:{args.port}")
    print(f"Hub API endpoint: http://{args.host}:{args.port}/api/hub/")
    print("")
    print("Test endpoints:")
    print(f"  GET    http://{args.host}:{args.port}/api/hub/admin (admin status page)")
    print(f"  POST   http://{args.host}:{args.port}/api/hub/")
    print("")
    print(f"Auth mode: {HUB_AUTH_MODE}")
    if HUB_AUTH_MODE == "oidc":
        print(f"  GET    http://{args.host}:{args.port}/oauth/login")
        print(f"  GET    http://{args.host}:{args.port}/oauth/callback")
        print(f"  GET    http://{args.host}:{args.port}/api/hub/auth/config")
        if HUB_OIDC_ISSUER:
            print(f"  OIDC issuer: {HUB_OIDC_ISSUER}")
    elif HUB_MOCK_OAUTH:
        print(f"  GET    http://{args.host}:{args.port}/oauth/login (JSON tokens)")
        print(f"  GET    http://{args.host}:{args.port}/api/hub/auth/config")
    else:
        print("  Mock OAuth disabled (HUB_MOCK_OAUTH=false)")
    if HUB_REQUIRE_AUTH:
        print("  Bearer auth: required on /api/hub, conference mutators, payloads, bind")
    if HUB_ADMIN_TOKEN:
        print("  Admin APIs: gated by HUB_ADMIN_TOKEN (X-Hub-Admin-Token / ?admin_token=)")
    elif HUB_REQUIRE_AUTH:
        print("  Admin APIs: gated by Bearer JWT (HUB_REQUIRE_AUTH)")
    print("")
    print(f"WebSocket connections: ws://{args.host}:{args.port}/bind/<endpoint>")
    print("")
    print("Using FastAPI with built-in WebSocket support")
    print("Press Ctrl+C to stop the server")
    print("=" * 60)

    # Run the server
    # Note: On Windows, if Ctrl+C still hangs after shutdown closes sockets:
    #   - Press Ctrl+C twice (second press forces interrupt)
    #   - Use Ctrl+Break instead
    #   - Or use: taskkill /F /PID <process_id>
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            ws_max_size=None,
            ws_ping_interval=HUB_UVICORN_WS_PING_INTERVAL,
            ws_ping_timeout=HUB_UVICORN_WS_PING_TIMEOUT,
            timeout_graceful_shutdown=5,
        )
    except KeyboardInterrupt:
        print("\n[LOG] Server stopped by user")


if __name__ == "__main__":
    main()
