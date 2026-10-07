# MHub.ai Hub service provider

Standalone Python service provider stub for [MHub.ai](https://mhub.ai)
([GitHub org](https://github.com/MHubAI/)). It receives a study (`dicom-send` /
`nifti-send`), reports progress via `status-update`, and ends with
`Job complete`.

The shipped script (`mhub.py`) is a **stub**: download + simulated processing
only. Replace marked sections later with real MHub Docker / model runs.

Vendored beside the stub (unused for now):

- [`MHubSkill/`](https://github.com/MHubAI/MHubSkill) — agent skill for model
  discovery, SegDB lookup, and workflow config generation.

Framework: `service_provider.py` (shared with other service providers).

## Slicer Hub setup

In **Service Providers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `MHUB` |
| Version | `1.0` |
| Description | e.g. MHub.ai model hub |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `service_providers/products/mhub/mhub.py` |

## Run standalone (no Slicer UI)

From the repo root (`HubInterface/`):

```bash
pip install aiohttp
python service_providers/products/mhub/mhub.py
python service_providers/products/mhub/mhub.py --local
```

Default hub is **SLICER-HUB-CLOUD**; `--local` uses `http://127.0.0.1:2018`.
