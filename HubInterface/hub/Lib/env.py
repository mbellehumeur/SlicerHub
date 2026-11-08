"""Hub environment flags and tunables."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

# Default hub-request fan-out timeout (seconds). Collated HTTP reply returns
# when all targets respond or this cap is reached (partial + timedOut ok).
HUB_REQUEST_TIMEOUT_SECONDS = float(os.getenv("HUB_REQUEST_TIMEOUT_SECONDS", "2"))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def cors_allow_config() -> Tuple[List[str], bool]:
    """Return ``(allow_origins, allow_credentials)`` for CORSMiddleware.

    ``HUB_CORS_ORIGINS`` is a comma-separated origin list, or ``*`` (default).
    Browsers forbid ``Access-Control-Allow-Origin: *`` together with
    ``Allow-Credentials: true``, so credentials are enabled only when origins
    are explicitly pinned. Hub JS clients use Bearer tokens with
    ``credentials: "omit"``, so open ``*`` without credentials is correct.
    """
    raw = os.environ.get("HUB_CORS_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"], False
    origins = [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]
    if not origins or origins == ["*"]:
        return ["*"], False
    return origins, True


HUB_WS_KEEPALIVE = _env_flag("HUB_WS_KEEPALIVE", default=False)
HUB_WS_KEEPALIVE_INTERVAL_SECONDS = float(
    os.getenv("HUB_WS_KEEPALIVE_INTERVAL_SECONDS", "30")
)


def _env_optional_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _env_uvicorn_ws_ping_seconds(name: str, default: float) -> Optional[float]:
    """Return ping interval/timeout in seconds; ``0`` disables; unset uses ``default``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else None


HUB_UVICORN_WS_PING_INTERVAL = _env_uvicorn_ws_ping_seconds(
    "HUB_UVICORN_WS_PING_INTERVAL_SECONDS", 20.0
)
HUB_UVICORN_WS_PING_TIMEOUT = (
    _env_uvicorn_ws_ping_seconds(
        "HUB_UVICORN_WS_PING_TIMEOUT_SECONDS",
        HUB_UVICORN_WS_PING_INTERVAL or 20.0,
    )
    or HUB_UVICORN_WS_PING_INTERVAL
)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Multipart binary ingest: publishers POST ``multipart/related`` (Hub JSON +
# file parts). The hub stores bytes under short-lived ``payloadId`` values and
# fans out text-only WebSocket JSON with ``context.files[].payloadIds[]`` and
# ``chunkByteLengths[]``. Subscribers GET ``/api/hub/payloads/{payloadId}``
# when the application chooses to download.
HUB_HTTP_PAYLOAD_TTL_SECONDS = _env_positive_int(
    "HUB_HTTP_PAYLOAD_TTL_SECONDS", 1800
)
# Soft cap on total in-flight payload bytes. If a registration would exceed
# this, the hub strips the http marker and fans out metadata-only JSON.
HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES = _env_positive_int(
    "HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES", 2 * 1024 * 1024 * 1024
)
# Max bytes per in-memory stored chunk. Larger binary batch files are split into
# multiple payloadId entries on fan-out (default 4 MiB). 0 = one chunk per file.
HUB_HTTP_PAYLOAD_STORE_CHUNK_BYTES = _env_positive_int(
    "HUB_HTTP_PAYLOAD_STORE_CHUNK_BYTES", 4 * 1024 * 1024
)
# Chunk size when streaming GET /api/hub/payloads/{token} bodies. Avoids
# Response(content=bytes) which can be throttled by BaseHTTPMiddleware-style
# body teeing and many small uvicorn writes on Windows.
HUB_HTTP_PAYLOAD_SEND_CHUNK_BYTES = _env_positive_int(
    "HUB_HTTP_PAYLOAD_SEND_CHUNK_BYTES", 4 * 1024 * 1024
)
