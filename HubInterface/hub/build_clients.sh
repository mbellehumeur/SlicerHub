#!/usr/bin/env bash
set -euo pipefail

# Workspace root: .../src (parent of SlicerHub/)
WS="$(cd "$(dirname "$0")/../../.." && pwd)"

# SlicerHub-js-clients apps + OHIF extension sources
cd "$WS/SlicerHub-js-clients" && npm install && npm run build:apps

# OHIF 3.14 + @slicer-hub/ohif-extension → platform/app/dist (/ohif/)
cd "$WS/Viewers/platform/app" && pnpm run build:slicer-hub

# Optional VolView / Slim — keep if present in workspace
if [[ -d "$WS/VolView" ]]; then
  cd "$WS/VolView" && npm run build
fi
if [[ -d "$WS/slim" ]]; then
  cd "$WS/slim" && MSYS_NO_PATHCONV=1 pnpm run build:cast
fi

cd "$WS/SlicerHub/HubInterface/hub" && python make_zip.py --skip-build
