# SlicerHub

3D Slicer **Hub Interface** extension: hub, resource servers, image display, and Python client under `HubInterface/` — runnable **without** opening the module UI.

**GitHub:** [mbellehumeur/SlicerHub](https://github.com/mbellehumeur/SlicerHub)

## Layout

| Path | Purpose |
|------|---------|
| [`HubInterface/`](HubInterface/) | Slicer module package (`HubInterface.py`, `Lib/`, `Resources/`, bundled runtime) |
| `HubInterface/hub/` | FastAPI Slicer hub (`hub.py`) |
| `HubInterface/resource_servers/` | Resource-server framework + products |
| `HubInterface/image_display/` | Slicer image display runtime + CLI |
| `HubInterface/python_client/` | Python `hub_client` package |
| [`docs/`](docs/) | Extension docs (module docs under `docs/module/`) |

CMake install copies `HubInterface/` into `qt-scripted-modules/HubInterface/` (module scripts + bundled folders).

## Quick start (no Slicer Hub module)

```bash
cd HubInterface

# One-time: shared Python client
pip install -e python_client

# Terminal 1 — Hub
cd hub && pip install -r requirements.txt && python hub.py --port 2018

# Terminal 2 — Resource server (examples)
python resource_servers/products/neuro_seg.py --local
# python resource_servers/products/total_segmentator.py --local

# Terminal 3 — Slicer image display (Slicer required; module NOT required)
Slicer --python-script image_display/run_image_display.py -- --local --topic USER-1
```

Admin UI: http://127.0.0.1:2018/api/hub/admin

See [docs/standalone-components.md](docs/standalone-components.md) for details.

## 3D Slicer extension

Build from this repo (`CMakeLists.txt` at repo root, module in `HubInterface/`). For dev, add the **repo root** to Additional module paths — Slicer discovers `HubInterface/HubInterface.py`.

Optional sync into an installed module directory:

```bash
export SLICER_HUB_EXTENSION="/path/to/qt-scripted-modules/HubInterface"
python tools/sync_extension.py --bundle
```

Override module root at runtime with `HUB_REPO_ROOT` when needed.

## License

MIT — see [LICENSE](LICENSE).
