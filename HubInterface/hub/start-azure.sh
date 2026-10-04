#!/usr/bin/env bash
set -euo pipefail

# Azure App Service injects PORT for Linux containers.
PORT="${PORT:-8000}"
HOST="0.0.0.0"

# Zip deploy may skip Oryx pip install (no antenv). Docker images already have deps.
if ! python -c "import aiohttp" >/dev/null 2>&1; then
  if [[ -f requirements.txt ]]; then
    echo "Installing Python dependencies from requirements.txt..."
    python -m pip install --no-cache-dir -r requirements.txt
  else
    echo "WARNING: requirements.txt not found; hub may fail on missing imports." >&2
  fi
fi

# Optional hub settings (override from App Settings as needed).
export HUB_WS_KEEPALIVE="${HUB_WS_KEEPALIVE:-true}"
export HUB_WS_KEEPALIVE_INTERVAL_SECONDS="${HUB_WS_KEEPALIVE_INTERVAL_SECONDS:-30}"
export HUB_UVICORN_WS_PING_INTERVAL_SECONDS="${HUB_UVICORN_WS_PING_INTERVAL_SECONDS:-20}"
export HUB_UVICORN_WS_PING_TIMEOUT_SECONDS="${HUB_UVICORN_WS_PING_TIMEOUT_SECONDS:-20}"
export HUB_HTTP_PAYLOAD_TTL_SECONDS="${HUB_HTTP_PAYLOAD_TTL_SECONDS:-300}"
export HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES="${HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES:-2147483648}"
export HUB_FILENAME_POLICY="${HUB_FILENAME_POLICY:-on}"

echo "Starting Slicer Hub on ${HOST}:${PORT}"
exec python hub.py --host "${HOST}" --port "${PORT}"
