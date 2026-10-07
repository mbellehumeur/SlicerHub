# AGENTS.md — SlicerHub

## Hub server (authoritative)

Change hub server code only in **`HubInterface/hub/`** (`hub.py`). Legacy Cast paths (`SlicerCastInterface`, `VolView/server/cast_api/`) are out of scope.

Default local port: **2018** (`python HubInterface/hub/hub.py --port 2018`).

## Monorepo layout (all-in HubInterface/)

| Path | Role |
|------|------|
| `CMakeLists.txt` | Extension superbuild at repo root |
| `HubInterface/` | Slicer module package (discovery: `HubInterface/HubInterface.py`) |
| `HubInterface/Lib/` | Module UI helpers |
| `HubInterface/hub/` | FastAPI hub |
| `HubInterface/service_providers/` | Service-provider framework + `products/` |
| `HubInterface/image_display/` | Slicer ID runtime + `run_image_display.py` |
| `HubInterface/python_client/` | Python `hub_client` (`pip install -e python_client` from `HubInterface/`) |

Runtime path resolution: `HubInterface/Lib/repo_paths.py` — `extension_root()` / `repo_root()` = module dir (contains bundled `hub/`). Optional `HUB_REPO_ROOT` override.

Dev copy helper: `tools/sync_extension.py` (not a Slicer module).

## Line endings

LF only for all text files.

## Frozen downstream (v0.1)

Do not edit vtk-js, VolView, OHIF for Hub work in this migration phase.

## Event-name parity

When wire helpers change, update `python_client`, vtk-js `eventNames.js`, and hub handlers together.
