#!/usr/bin/env bash
# Deploy Slicer Hub to Fly.io (mock and/or OIDC apps).
#
# Usage:
#   ./deploy-fly.sh              # default: mock
#   ./deploy-fly.sh mock
#   ./deploy-fly.sh oidc
#   ./deploy-fly.sh all
#   ./deploy-fly.sh all --yes    # extra fly deploy flags after the target
#
# Prereqs:
#   - flyctl installed and logged in (fly auth login)
#   - SPA mounts present under hub/ (website/, worklist-client/, OHIF-client/, …)
#     Sync with:  python make_zip.py --skip-build
#     (fix OHIF PUBLIC_URL=/ohif/ first if make_zip rejects Viewers dist)
#
# --- Mock app (bellehumeur-hub → https://bellehumeur-engineering.com) ---
#   fly secrets set -a bellehumeur-hub \
#     HUB_AUTH_MODE=mock \
#     HUB_PUBLIC_BASE_URL=https://bellehumeur-engineering.com \
#     HUB_WS_KEEPALIVE=true
#   fly certs add -a bellehumeur-hub bellehumeur-engineering.com
#   fly certs add -a bellehumeur-hub www.bellehumeur-engineering.com
#   # GoDaddy: apex/www → Fly; keep Fastmail MX
#
# --- OIDC app (bellehumeur-hub-oidc → https://oidc.bellehumeur-engineering.com) ---
#   fly secrets set -a bellehumeur-hub-oidc \
#     HUB_AUTH_MODE=oidc \
#     HUB_OIDC_ISSUER=https://YOUR_TENANT.us.auth0.com \
#     HUB_OIDC_CLIENT_ID=... \
#     HUB_OIDC_CLIENT_SECRET=... \
#     HUB_JWT_SECRET=... \
#     HUB_PUBLIC_BASE_URL=https://oidc.bellehumeur-engineering.com \
#     HUB_OIDC_REDIRECT_URI=https://oidc.bellehumeur-engineering.com/oauth/callback \
#     HUB_WS_KEEPALIVE=true
#   fly certs add -a bellehumeur-hub-oidc oidc.bellehumeur-engineering.com
#   # GoDaddy: oidc CNAME → bellehumeur-hub-oidc.fly.dev
#   # Auth0 callbacks/logout/web origins: oidc host only
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HUB_IFACE="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET="mock"
FLY_ARGS=()
if [[ $# -gt 0 ]]; then
  case "$1" in
    mock|oidc|all)
      TARGET="$1"
      shift
      ;;
  esac
fi
FLY_ARGS=("$@")

cd "$SCRIPT_DIR"
if [[ ! -f website/index.html ]]; then
  echo "Warning: hub/website/index.html missing — marketing site at / will 404." >&2
  echo "  Build: cd bellehumeur-website && python build.py" >&2
  echo "  Sync:  python make_zip.py --skip-build   (from this directory)" >&2
fi
if [[ ! -f OHIF-client/index.html ]]; then
  echo "Warning: hub/OHIF-client/index.html missing — /ohif/ will not be served." >&2
fi
if [[ ! -d "$HUB_IFACE/python_client/src/hub_client" ]]; then
  echo "Error: HubInterface/python_client missing (needed by Dockerfile.fly)." >&2
  exit 1
fi

cd "$HUB_IFACE"
deploy_one() {
  local config="$1"
  echo "=== fly deploy -c ${config} ==="
  fly deploy -c "$config" "${FLY_ARGS[@]}"
}

case "$TARGET" in
  mock)
    deploy_one fly.toml
    ;;
  oidc)
    deploy_one fly.oidc.toml
    ;;
  all)
    deploy_one fly.toml
    deploy_one fly.oidc.toml
    ;;
  *)
    echo "Usage: $0 [mock|oidc|all] [fly deploy flags...]" >&2
    exit 1
    ;;
esac
