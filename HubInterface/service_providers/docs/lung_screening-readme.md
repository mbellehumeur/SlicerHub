# Lung screening Hub service provider

Standalone Python service provider. It receives a study from VolView or OHIF (`dicom-send` or `nifti-send`), runs chest CT lung screening inference, and reports progress back to the requester via `status-update`.

The shipped script (`lung_screening.py`) is a **stub**: it downloads files, simulates
10 seconds of processing, and publishes `RESULT: NEGATIVE`. Replace the marked sections
with your conversion, CSV preparation, inference, and result publishing code.

Framework: `service_provider.py` (shared with other service providers).

## Slicer Hub setup

In **Service Providers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `LUNGSCREENING` |
| Version | `1.0` |
| Description | e.g. NLST lung screening inference |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `service_providers/products/lung_screening.py` |

Click **Connect**. The subscriber name (`LUNGSCREENING-XXXXXX`) appears in the hub admin
portal. Subscribed events: `dicom-send`, `nifti-send`, `status-request`.

On `status-request`, the server answers with `{ source: "status", product, items: [{ availability: online }] }` (`build_status_response` in `lung_screening.py`). VolView and OHIF use this to enable the **Lung screening** dialog **Send** button.

## Run standalone (no Slicer UI)

From the repo root:

```bash
pip install aiohttp
python service_providers/products/lung_screening.py
python service_providers/products/lung_screening.py --local
```

Default hub is **SLICER-HUB-CLOUD**; `--local` uses `http://127.0.0.1:2018`.

## End-to-end job flow

```
VolView / OHIF                    Hub                         lung_screening.py
     |                             |                                  |
     |  dicom-send or nifti-send   |                                  |
     |---------------------------->|--------------------------------->|
     |                             |     on_send_download_start       |
     |  status-update (download)   |<---------------------------------|
     |<----------------------------|                                  |
     |                             |     download files -> input_dir    |
     |                             |     on_dicom_send / on_nifti_send  |
     |  status-update (each step)  |<---------------------------------|
     |<----------------------------|                                  |
     |  status-update (RESULT: …)  |<---------------------------------|
     |<----------------------------|                                  |
     |  optional dicom-send result |<---------------------------------|
     |<----------------------------|                                  |
```

Inbound files land under a per-job temp directory, e.g.:

`%TEMP%/hub-rs/LUNGSCREENING/<topic>-<timestamp>/input/`

The handler receives that path as `input_dir`.

## `status-update` (job log to requester)

One-way publish (not request/response). VolView and OHIF append each line to the **Job
Status** textarea in the Lung screening dialog.

| Field | Value |
|-------|--------|
| `hub.event` | `status-update` |
| `target.subscriber.name` | Requester from inbound send (`subscriber.name`, e.g. `VolView-ABC123`) |
| `event.context.message` | Human-readable line |
| `event.context.level` | `info` or `error` (optional) |

Use `_publish_to_requester(ctx, message, status_line)` or `ctx.publish_status_update_sync(topic, target_subscriber, status_line)` after **every** major step (see extension points below).

Typical sequence once fully implemented:

```
Downloading CT study, 212 files, total 105 MB.
Download complete.
Converting DICOM to NIfTI…
NIfTI ready: study.nii.gz
Writing input.csv…
Running inference…
Processing
RESULT: NEGATIVE
Publishing result…
Job finished.
```

## Where to add your code

All customization lives in **`lung_screening.py`**. The table maps pipeline steps to
functions and line regions (approximate; check the file after edits).

| Step | Where | Function / region |
|------|--------|-------------------|
| Status before download | Already implemented | `on_send_download_start` |
| Handler entry after download | Replace stub body | `_handle_inbound_send` |
| DICOM → NIfTI | **Add here** | New helper, called from `_handle_inbound_send` when label is `dicom-send` |
| Create `input.csv` | **Add here** | New helper, after NIfTI path is known |
| Run inference | **Replace** | `_simulate_processing` → your inference call |
| Publish final result text | **Replace** | `_publish_to_requester(..., SCREENING_RESULT_LINE)` |
| Publish result file (optional) | **Add here** | `ctx.publish_dicom_send` / `publish_nifti_send` |
| Status after each operation | **Add calls** | `_publish_to_requester` throughout |

### 1. Status before download (already done)

`on_send_download_start` runs **before** the framework downloads bytes. It reads
`context.files[]` from the inbound message and sends:

`Downloading CT study, {n} files, total {mb} MB.`

No changes required unless you want different wording.

### 2. After download — start of `_handle_inbound_send`

**File:** `lung_screening.py` — `_handle_inbound_send` (called from `on_dicom_send` and
`on_nifti_send`).

At this point:

- `input_dir` contains all downloaded DICOM slices or the NIfTI file.
- `ctx.write_directory_manifest(input_dir)` already wrote `downloaded-files.txt` beside
  `input/`.

**Add** a status line immediately after the manifest write:

```python
_publish_to_requester(ctx, message, "Download complete.")
```

Then branch on `label` (`"dicom-send"` vs `"nifti-send"`) to decide whether conversion is
needed.

### 3. Conversion to NIfTI (after download)

**Add a new function**, e.g. `_convert_dicom_dir_to_nifti(input_dir: Path) -> Path`.

Call it from `_handle_inbound_send` when `label == "dicom-send"`. Skip when the inbound
event is `nifti-send` and use the file in `input_dir` directly.

```python
def _convert_dicom_dir_to_nifti(input_dir: Path) -> Path:
    # TODO: your DICOM series → single NIfTI volume
    # e.g. dicom2nifti, pydicom + nibabel, or subprocess to dcm2niix
    nifti_path = input_dir.parent / "study.nii.gz"
    _publish_to_requester(...)  # optional: "Converting DICOM to NIfTI…"
    # ... conversion ...
    return nifti_path
```

Send `status-update` before and after conversion:

```python
_publish_to_requester(ctx, message, "Converting DICOM to NIfTI…")
nifti_path = _convert_dicom_dir_to_nifti(input_dir)
_publish_to_requester(ctx, message, f"NIfTI ready: {nifti_path.name}")
```

### 4. Creation of `input.csv`

The McConnell et al. chest CT foundation model expects a CSV listing cases to score.
**Add a new function**, e.g. `_write_input_csv(nifti_path: Path, job_dir: Path) -> Path`.

```python
def _write_input_csv(nifti_path: Path, job_dir: Path) -> Path:
    # TODO: match your model's expected columns (path, study_id, …)
    csv_path = job_dir / "input.csv"
    _publish_to_requester(...)  # "Writing input.csv…"
    # Example single-row batch:
    # case_id,image_path
    # study_001,/path/to/study.nii.gz
    csv_path.write_text(
        "case_id,image_path\n"
        f"study_001,{nifti_path.resolve()}\n",
        encoding="utf-8",
    )
    return csv_path
```

Call from `_handle_inbound_send` after the NIfTI path is known. Publish status when the
file is written.

### 5. Calling inference

**Replace `_simulate_processing`** with a function that runs your model, e.g.
`_run_inference(csv_path: Path, output_dir: Path) -> str`.

The stub sleeps and sends `Processing` every 3 seconds. Keep periodic `status-update`
lines during long runs so VolView/OHIF show liveness:

```python
def _run_inference(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    csv_path: Path,
    output_dir: Path,
) -> str:
    _publish_to_requester(ctx, message, "Running inference…")
    output_dir.mkdir(parents=True, exist_ok=True)

    # TODO: subprocess or Python API, e.g.:
    # subprocess.run(["python", "-m", "your_model", "--input", str(csv_path), ...], check=True)

    # Parse model output → human-readable result line for the dialog:
    result_line = "RESULT: NEGATIVE"  # or RESULT: POSITIVE, scores, etc.
    return result_line
```

Wire it in `_handle_inbound_send` instead of `_simulate_processing(ctx, message)`.

### 6. Sending the result back

#### Text result (`status-update`)

After inference, publish the summary line (replaces `SCREENING_RESULT_LINE` stub):

```python
result_line = _run_inference(ctx, message, csv_path, job_dir / "output")
_publish_to_requester(ctx, message, result_line)
```

Use `level="error"` for failures:

```python
ctx.publish_status_update_sync(
    topic, target_subscriber, f"ERROR: {exc}", level="error"
)
```

#### Optional binary result (`dicom-send` / `nifti-send`)

To push a segmentation mask, report, or derived volume back to the topic, publish after
the text result. Handlers run on a worker thread; schedule the async publish on
`ctx.loop`:

```python
import asyncio

def _publish_result_file_sync(
    ctx: ServiceProviderContext, topic: str, result_path: Path
) -> None:
    future = asyncio.run_coroutine_threadsafe(
        ctx.publish_dicom_send(topic, str(result_path)),  # or publish_nifti_send
        ctx.loop,
    )
    http_status = future.result(timeout=120.0)
    LOGGER.info("LUNGSCREENING: published result HTTP %s", http_status)
```

Call from `_handle_inbound_send` when a result file exists. Send
`_publish_to_requester(ctx, message, "Publishing result…")` before and
`Job finished.` after.

Stub comments at the bottom of `lung_screening.py` (lines ~193–200) point to these APIs.

### 7. Suggested `_handle_inbound_send` skeleton

Replace the simulate + fixed result block with something like:

```python
def _handle_inbound_send(...):
    # ... existing logging and manifest ...
    _publish_to_requester(ctx, message, "Download complete.")

    job_dir = input_dir.parent
    if label == "dicom-send":
        nifti_path = _convert_dicom_dir_to_nifti(input_dir)
    else:
        nifti_files = sorted(input_dir.glob("*.nii*"))
        if not nifti_files:
            _publish_to_requester(ctx, message, "ERROR: no NIfTI file in input")
            return
        nifti_path = nifti_files[0]

    csv_path = _write_input_csv(nifti_path, job_dir)
    result_line = _run_inference(ctx, message, csv_path, job_dir / "output")
    _publish_to_requester(ctx, message, result_line)

    # optional:
    # result_dcm = job_dir / "output" / "report.dcm"
    # if result_dcm.is_file():
    #     _publish_to_requester(ctx, message, "Publishing result…")
    #     _publish_result_file_sync(ctx, topic, result_dcm)
    #     _publish_to_requester(ctx, message, "Job finished.")
```

## Input expectations

### `dicom-send`

VolView/OHIF send URL-only manifests (`context.files[].url`) for IDC lung screening studies
(DICOMweb primary; direct S3 URLs as fallback). The framework downloads each file into
`input_dir`. Expect a **full CT series** (many slices).

### `nifti-send`

One compressed NIfTI (`.nii.gz`) per message. No DICOM conversion step.

## Related files

| File | Role |
|------|------|
| `lung_screening.py` | Product handlers — **edit here** |
| `service_provider.py` | Hub connect, download, `ServiceProviderContext` publish helpers |
| `python_client/src/hub_client/client.py` | Hub wire protocol (used via `service_provider`) |

See also `totalsegmentator-readme.md` for a fully wired service-provider example with job
logging and result publish.
