#!/usr/bin/env bash
set -euo pipefail

WS="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "$WS/ProjectWeek45/vtk-js" && npm run buildCast
cd "$WS/ProjectWeek45/VolView" && npm run build
cd "$WS/ProjectWeek45/Viewers" && yarn build
cd "$WS/ProjectWeek45/Viewers/platform/app" && yarn build:slicer-hub
cd "$WS/slim" && MSYS_NO_PATHCONV=1 REACT_APP_CONFIG=cast PUBLIC_URL=/slim/ ./scripts/set-git-env.sh ./node_modules/.bin/craco build
cd "$WS/SlicerHub/HubInterface/hub" && python make_zip.py
