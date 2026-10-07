# Neurosegmentation Hub resource server

Standalone Python resource server. It receives a NIfTI volume from VolView or OHIF (`nifti-send`), runs neurosegmentation inference, reports progress back to the requester via `status-update` and ends with sending a nifti back to the OHIF/VolView.

The shipped script (`neuro_seg.py`) is a **stub**: it downloads the NIfTI file, simulates 10 seconds of processing, and publishes `Segmentation complete`. Replace the marked sections with your inference and result publishing code.


Framework: `resource_server.py` (shared with other resource servers).

## Slicer Hub setup

In **Resource Servers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `NEURO_SEG` |
| Version | `1.0` |
| Description | e.g. neurosegmentation |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `resource_servers/products/neuro_seg.py` |

Click **Connect**. The subscriber name (`NEURO_SEG-XXXXXX`) appears in the hub admin
portal. Subscribed events: `nifti-send`, `status-request` (no `dicom-send`).

On `status-request`, the server answers with `{ source: "status", product, items: [{ availability: online }] }` (`build_status_response` in `neuro_seg.py`).

**Note:** There is no VolView or OHIF dialog for `NEURO_SEG` yet. Clients must send `nifti-send` with `target.product.name: NEURO_SEG` (or omit the product filter for fan-out).

## Run standalone (no Slicer UI)

From the repo root:

```bash
pip install aiohttp
python resource_servers/products/neuro_seg.py
python resource_servers/products/neuro_seg.py --local
```

Default hub is **SLICER-HUB-CLOUD**; `--local` uses `http://127.0.0.1:2018`.

## End-to-end job flow

```
VolView / OHIF                    Hub                         neuro_seg.py
     |                             |                                  |
     |  nifti-send                 |                                  |
     |---------------------------->|--------------------------------->|
     |                             |     on_send_download_start       |
     |  status-update (download)   |<---------------------------------|
     |<----------------------------|                                  |
     |                             |     download files -> input_dir  |
     |                             |     on_nifti_send                  |
     |  status-update (each step)  |<---------------------------------|
     |<----------------------------|                                  |
     |  status-update (complete)   |<---------------------------------|
     |<----------------------------|                                  |
     |  optional nifti-send result |<---------------------------------|
     |<----------------------------|   (commented out in stub)        |
```

Inbound files land under a per-job temp directory, e.g.:

`%TEMP%/hub-rs/NEURO_SEG/<topic>-<timestamp>/input/`

The handler receives that path as `input_dir`.

## `status-update` (job log to requester)

One-way publish (not request/response). VolView and OHIF append each line to the **Job
Status** textarea when a matching dialog exists.

| Field | Value |
|-------|--------|
| `hub.event` | `status-update` |
| `target.subscriber.name` | Requester from inbound send (`subscriber.name`, e.g. `VolView-ABC123`) |
| `event.context.message` | Human-readable line |
| `event.context.level` | `info` or `error` (optional) |

Use `_publish_to_requester(ctx, message, status_line)` or `ctx.publish_status_update_sync(topic, target_subscriber, status_line)` after **every** major step (see extension points below).

Typical sequence once fully implemented:

```
Downloading NIfTI volume, 1 files, total 50 MB.
Download complete.
Processing
Segmentation complete
```

After you uncomment the result publish block in `neuro_seg.py`, the sequence continues:

```
Publishing result…
Job finished.
```

## Where to add your code

All customization lives in **`neuro_seg.py`**.

| Step | Where | Function / region |
|------|--------|-------------------|
| Status before download | Already implemented | `on_send_download_start` |
| Handler entry after download | Replace stub body | `_handle_inbound_nifti_send` |
| Resolve input NIfTI | Already implemented | `_resolve_input_nifti` |
| Run inference | **Replace** | `_simulate_processing` → your inference call |
| Publish final status text | **Replace** | `_publish_to_requester(..., FINAL_STATUS_LINE)` |
| Publish result NIfTI | **Uncomment** | `_publish_result_nifti_sync` + `ctx.publish_nifti_send` |
| Status after each operation | **Add calls** | `_publish_to_requester` throughout |

### 1. Status before download (already done)

`on_send_download_start` runs **before** the framework downloads bytes. It reads
`context.files[]` from the inbound message and sends:

`Downloading NIfTI volume, {n} files, total {mb} MB.`

No changes required unless you want different wording.

### 2. After download — start of `_handle_inbound_nifti_send`

**File:** `neuro_seg.py` — `_handle_inbound_nifti_send` (called from `on_nifti_send`).

At this point:

- `input_dir` contains the downloaded NIfTI file (`.nii` or `.nii.gz`).
- `ctx.write_directory_manifest(input_dir)` already wrote `downloaded-files.txt` beside
  `input/`.
- `_resolve_input_nifti` picks the first `*.nii*` file in `input_dir`.

The stub already sends `Download complete.` after the manifest write.

### 3. Calling inference

**Replace `_simulate_processing`** with a function that runs your model, e.g.
`_run_inference(ctx, message, nifti_path: Path, output_dir: Path) -> Path`.

The stub sleeps and sends `Processing` every 3 seconds. Keep periodic `status-update`
lines during long runs so clients show liveness:

```python
def _run_inference(
    ctx: ResourceServerContext,
    message: Dict[str, Any],
    nifti_path: Path,
    output_dir: Path,
) -> Path:
    _publish_to_requester(ctx, message, "Running neurosegmentation…")
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "neuro_seg.nii.gz"

    # TODO: subprocess or Python API, e.g.:
    # subprocess.run(
    #     ["your-seg-tool", "--input", str(nifti_path), "--output", str(result_path)],
    #     check=True,
    # )

    return result_path
```

Wire it in `_handle_inbound_nifti_send` instead of `_simulate_processing(ctx, message)`.

### 4. Sending the result back

#### Text result (`status-update`)

After inference, publish the summary line (replaces the stub `FINAL_STATUS_LINE`):

```python
result_path = _run_inference(ctx, message, nifti_path, job_dir / "output")
_publish_to_requester(ctx, message, FINAL_STATUS_LINE)
```

Use `level="error"` for failures:

```python
ctx.publish_status_update_sync(
    topic, target_subscriber, f"ERROR: {exc}", level="error"
)
```

#### Binary result (`nifti-send`)

To push the segmentation volume back to the topic, uncomment the block at the bottom of
`_handle_inbound_nifti_send` and the `_publish_result_nifti_sync` helper in
`neuro_seg.py`. Handlers run on a worker thread; schedule the async publish on
`ctx.loop`:

```python
def _publish_result_nifti_sync(
    ctx: ResourceServerContext, topic: str, result_path: Path
) -> None:
    import asyncio

    future = asyncio.run_coroutine_threadsafe(
        ctx.publish_nifti_send(topic, str(result_path)),
        ctx.loop,
    )
    http_status = future.result(timeout=120.0)
    LOGGER.info("NEURO_SEG: published result HTTP %s", http_status)
```

Call after inference when `result_path` exists:

```python
job_dir = input_dir.parent
result_path = _run_inference(ctx, message, nifti_path, job_dir / "output")
_publish_to_requester(ctx, message, FINAL_STATUS_LINE)

if result_path.is_file():
    _publish_to_requester(ctx, message, "Publishing result…")
    _publish_result_nifti_sync(ctx, topic, result_path)
    _publish_to_requester(ctx, message, "Job finished.")
```

### 5. Suggested `_handle_inbound_nifti_send` skeleton

Replace the simulate + fixed status block with something like:

```python
def _handle_inbound_nifti_send(...):
    # ... existing logging, manifest, validation ...
    _publish_to_requester(ctx, message, "Download complete.")

    nifti_path = _resolve_input_nifti(input_dir)
    if nifti_path is None:
        _publish_to_requester(ctx, message, "ERROR: no NIfTI file in input")
        return

    job_dir = input_dir.parent
    result_path = _run_inference(ctx, message, nifti_path, job_dir / "output")
    _publish_to_requester(ctx, message, FINAL_STATUS_LINE)

    if result_path.is_file():
        _publish_to_requester(ctx, message, "Publishing result…")
        _publish_result_nifti_sync(ctx, topic, result_path)
        _publish_to_requester(ctx, message, "Job finished.")
```

## Input expectations

### `nifti-send`

One compressed NIfTI (`.nii.gz`) or uncompressed NIfTI (`.nii`) per message. The server
does not accept DICOM — send a pre-converted brain MRI volume.

VolView/OHIF may send URL-only manifests (`context.files[].url`) or hub `payloadIds`;
the framework downloads each file into `input_dir`.

## Output

- **Text:** final `status-update` line `Segmentation complete` (stub).
- **Binary (when wired):** segmentation label map as `nifti-send` on the same hub topic.

## Related files

| File | Role |
|------|------|
| `neuro_seg.py` | Product handlers — **edit here** |
| `resource_server.py` | Hub connect, download, `ResourceServerContext` publish helpers |
| `python_client/src/hub_client/client.py` | Hub wire protocol (used via `resource_server`) |

See also `totalsegmentator-readme.md` for a fully wired resource-server example with job
logging and result publish.
