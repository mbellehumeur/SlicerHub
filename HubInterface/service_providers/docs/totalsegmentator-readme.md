# TotalSegmentator Hub service provider

## Standalone CLI (no Slicer UI)

From `HubInterface/` with a hub already listening (e.g. `--port 2018`):

```bash
pip install -e python_client
pip install aiohttp
# Inference deps are operator-managed (TotalSegmentator / torch / highdicom), or use PythonSlicer on PATH
python service_providers/products/total_segmentator.py --local
```

`--local` connects to `http://127.0.0.1:2018`. Omit it to use the default cloud hub.

The CLI reuses the same job body as the Slicer **onMessage** path (`dicom-send` / `nifti-send` → status-update → result publish). Prefer the `TotalSegmentator` console script (including user `Scripts` dirs) or `PythonSlicer`; otherwise `python -m totalsegmentator.bin.TotalSegmentator` (not `-m totalsegmentator` — that package has no `__main__`). GPU/CUDA follows whichever Python runs the CLI. DICOM SEG output (`-ot dicom_seg`) requires **`highdicom`** in that same environment.

## Slicer Hub setup

In **Service Providers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `TOTALSEG` |
| Version | `1.0` |
| Description | e.g. Total Segmentator CT segmentation |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `service_providers/products/total_segmentator.py` |

Click **Connect**. The subscriber name (`TOTALSEG-XXXXXX`) appears only in the **hub admin portal**. Hub events subscribed for `TOTALSEG`: `dicom-send`, `nifti-send`, `status-request`.

On `status-request`, the service provider hub answers with `{ source: "status", product, items: [{ availability: online }] }`. While a segmentation job is running, `items` also includes `{ key: "job", value: "running" }` (see `build_status_response` in `total_segmentator.py`).

### `status-update` (job log to requester)

During segmentation, TotalSegmentator publishes user-facing progress lines to the VolView
subscriber that sent the study. This is a **one-way publish** (not request/response).
Verbose debug (JSON dumps, temp paths, CLI argv) stays in the Slicer Python console only.

| Field | Value |
|-------|--------|
| `hub.event` | `status-update` |
| `target.subscriber.name` | Requester from inbound `nifti-send` / `dicom-send` (`subscriber.name`, e.g. `VolView-ABC123`) |
| `event.context.message` | Human-readable line, e.g. `[14:32:05] Job #1: Download started: study.nii.gz (50.1 MB)` |
| `event.context.level` | `info` or `error` (optional) |

Typical sequence:

```
[14:32:05] Job #1: Download started: study.nii.gz (50.1 MB)
[14:32:08] Job #1: Download complete: study.nii.gz (50.1 MB)
[14:32:08] Job #1: Segmentation started
… TotalSegmentator CLI stdout …
[14:34:30] Job #1: Segmentation finished (gpu, 142s)
[14:34:31] Job #1: Publishing result…
[14:34:32] Job #1: Job finished at 14:34:32
```

Multi-file `dicom-send` batches summarize file names and total size, e.g.
`Job #1: Download started: 142 files, 68.4 MB total (IM-0001.dcm … IM-0142.dcm)`.

The hub delivers only to the subscription whose `subscriber` matches
`target.subscriber.name` (case-insensitive). Omit or `*` = fan-out to all matching
topic/event subscribers (default publish behavior).

VolView subscribes to `status-update`, appends each `context.message` to the **Job Status**
textarea in the Total Segmentator dialog, and leaves the dialog open after upload so lines
stream in live. Non-VolView sends (empty requester) still log locally only.

**Disconnect the AIBRAIN service provider** while testing TotalSegmentator. If both are connected, AIBRAIN immediately publishes the demo `ai-results-mrbrain.dcm` on every `dicom-send` (the Hub module now skips that when multiple service providers are connected, but using one service provider avoids confusion).

Requires the **TotalSegmentator** Slicer extension (Python package `totalsegmentator`) when using the Slicer path. For plain-Python CLI, install TotalSegmentator into that environment (not pinned in `service_providers/requirements.txt`). DICOM SEG output also needs **`highdicom`**.

Inference runs in a **subprocess** (`TotalSegmentator` console script / `PythonSlicer` when available, else `python -m totalsegmentator.bin.TotalSegmentator`). That avoids Windows nnU-Net multiprocessing failures inside a live Slicer GUI process.

## Input expectations

### `dicom-send` (VolView / IRA binary batch)

- VolView/IRA sends one **`dicom-send`** per study/series (or slice selection) with **`context.files[]`** and one DICOM body per file (`multipart/related` on `POST /api/hub/`).
- The hub fans out metadata with **`payloadId`** per file; bytes are streamed into the job `input/` directory before handling.
- All files in the batch are staged under the **`hub.topic`** temp folder, then TotalSegmentator runs once (same pattern as `nifti-send`).
- Send a **complete CT series** (many slices); a single slice is unlikely to work.

### `nifti-send` (e.g. from VolView)

- One compressed NIfTI volume (`.nii.gz`) per message — whole study in one file.
- Segmentation runs when the NIfTI file is received.
- VolView publishes with `target.product.name` = `TOTALSEG`.
- VolView uploads via **multipart `POST /api/hub`**; subscribers download via `GET /api/hub/payloads/{payloadId}`.

## Output

- Always DICOM SEG (`-ot dicom_seg`), typically `segmentations.dcm` — including when the job arrived as `nifti-send`.
- Requires **highdicom** in the TotalSegmentator Python environment.
- Publishes that file back on the **same hub topic** as a **`dicom-send`** event (IRA reads SegmentSequence names / SNOMED / colors).

## Logs

- **VolView Job Status** — `status-update` lines prefixed with `Job #N:` (numbered per Slicer session).
- **Slicer Python console** — `TotalSegmentator:` debug lines (not sent to VolView).
