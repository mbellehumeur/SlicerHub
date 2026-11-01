# HubInterface Testing

Shared live and offline smoke scripts for the Hub extension:

- `hub/` — local hub HTTP / mock OAuth
- `python_client/` — `hub_client` subscribe / publish / request
- `service_providers/` — SP-like subscribe against the hub
- `image_display/` — offline ImagingStudy context helpers (no Slicer)

CMake still lives here for future `slicer_add_python_unittest` hooks.

## Prerequisites

```bash
cd HubInterface
pip install -e python_client
# aiohttp comes with hub-client
```

## Start local hub

```bash
cd HubInterface/hub
python hub.py --port 2018
```

## Run all live smokes (+ offline ID context)

```bash
cd HubInterface
python Testing/Python/run_live_smokes.py
```

Individual scripts:

```bash
python Testing/Python/hub/smoke_hub_http.py
python Testing/Python/python_client/smoke_client_pubsub.py
python Testing/Python/service_providers/smoke_sp_subscribe.py
python Testing/Python/image_display/test_imaging_study_context.py
python Testing/Python/python_client/test_event_names.py
```

Defaults: topic `USER-1`, endpoints from `SLICER-HUB` (`http://127.0.0.1:2018`).
