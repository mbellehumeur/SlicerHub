# Slicer image display client

Hub image display client for 3D Slicer (`imagingstudy-open`, `status-request`,
`livescene-request`, `dicom-request` → `dicom-send`, etc.).

Web viewers open Slicer-local DICOM studies by sending `dicom-request`; this client
exports files from `slicer.dicomDatabase` and publishes multipart `dicom-send`.

## MetaQuest mesh share

While connected, the client can publish visible models/closed-surface segments as
**`glb-send`** (binary GLB via Hub payloads) and answers **`glb-request`**. The
MetaQuest web viewer (`SLICERLIVE-METAQUEST`) loads those meshes and can enter WebXR.
Incremental appearance updates use **`meshscene-update`**.

**Credits:** The MetaQuest mesh-over-relay approach (GLB snapshots, device-side rendering,
Quest Browser WebXR) wcomes from 
[SlicerRemoteView](https://github.com/dolegadolegowski/SlicerRemoteView)
by **Damian Dołęga-Dołęgowski** (MIT). 

## Run without Slicer Hub module

Requires 3D Slicer with DICOM database open:

```bash
pip install -e python_client
Slicer --python-script image_display/run_image_display.py -- --local --topic USER-1
```

## Library

Runtime code lives in `lib/` (`image_display_client_hub.py`, loaders, handlers).
