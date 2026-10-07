# TorchXRayVision Hub service provider

Upstream: [mlmed/torchxrayvision](https://github.com/mlmed/torchxrayvision) — chest X-ray
pathology classification (18 findings) and lung/heart anatomy segmentation.

> **Status:** initial scaffold. `torchxrayvision.py` is a copy of
> `total_segmentator.py` running under product name `TORCHXRAYVISION`, so jobs
> currently behave exactly like TotalSegmentator (DICOM SEG result). Real
> torchxrayvision inference will replace the TotalSegmentator subprocess later.

## Standalone CLI (no Slicer UI)

From `HubInterface/` with a hub already listening (e.g. `--port 2018`):

```bash
pip install -e python_client
pip install aiohttp
# Until real inference lands: TotalSegmentator / torch / highdicom in this Python, or PythonSlicer on PATH
python service_providers/products/torchxrayvision/torchxrayvision.py --local
```

`--local` connects to `http://127.0.0.1:2018`. Omit it to use the default cloud hub.

## Slicer Hub setup

In **Service Providers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `TORCHXRAYVISION` |
| Version | `1.0` |
| Description | e.g. TorchXRayVision chest X-ray AI |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `service_providers/products/torchxrayvision/torchxrayvision.py` |

Hub events subscribed for `TORCHXRAYVISION`: `dicom-send`, `nifti-send`, `status-request`.

On `status-request` the server answers `{ source: "status", product, items: [{ availability: online }] }`,
plus `{ key: "job", value: "running" }` while a job runs.

## Product card

`torchxrayvision.info.json` (next to the script) is the catalog / info-card entry
(`id: txrv`). Copy it into hub-js-client with `node tools/sync_inference_info.js`.
The worklist button is `openTorchXrayVisionBtn` in the Remote AI row.

## Worklist cases

The Integrated Worklist org filter **TorchXRayVision** (`torchxrayvision`) lists
10 public IDC CR/DX chest radiographs (`worklist/torchxrayvision-manifest.json`).
Clicking the Remote AI TorchXRayVision button switches to that org. Cases open
via IDC direct S3 (`openMode: idc`). TorchXRayVision’s GitHub does not publish
IDC study links; these series are from `midrc_ricord_1c`, `covid_19_ny_sbu`
(Stony Brook thematic overlap), and `covid_19_ar`.

## Job flow, status-update, output

Same as TotalSegmentator — see [totalsegmentator-readme.md](totalsegmentator-readme.md).
Job temp dirs live under `<tmp>/hub-txrv-jobs/`.
