# Standalone Hub components

Run these from a clone of [SlicerHub](https://github.com/mbellehumeur/SlicerHub) **without** opening the Slicer Hub module in 3D Slicer.

All runtime folders live under **`HubInterface/`**.

## Prerequisites

```bash
cd HubInterface
pip install -e python_client
```

Python 3.9+ recommended.

---

## hub/

Hub / FHIRcast hub (FastAPI + WebSockets).

```bash
cd HubInterface/hub
pip install -r requirements.txt
python hub.py --port 2018
```

- Admin: http://127.0.0.1:2018/api/hub/admin  
- Cloud deploy: [HubInterface/hub/azure-webapp.md](../HubInterface/hub/azure-webapp.md)

---

## resource_servers/

Standalone resource servers (no Slicer UI).

```bash
cd HubInterface
pip install -r resource_servers/requirements.txt
python resource_servers/products/neuro_seg.py --local
python resource_servers/products/total_segmentator.py --local
```

`--local` uses `http://127.0.0.1:2018`. Default is cloud `SLICER-HUB-CLOUD`.

Examples: `neuro_seg.py`, `lung_screening.py`, `total_segmentator.py` (`TOTALSEG`). TotalSeg also needs TotalSegmentator installed in the Python that runs inference (or `PythonSlicer` on `PATH`); see [totalsegmentator-readme.md](../HubInterface/resource_servers/docs/totalsegmentator-readme.md). `torchxrayvision/torchxrayvision.py` (`TORCHXRAYVISION`) is currently a TotalSeg clone; see [torchxrayvision-readme.md](../HubInterface/resource_servers/docs/torchxrayvision-readme.md).

Product docs: [resource_servers/docs/](../HubInterface/resource_servers/docs/).

---

## image_display/

Slicer as an image display client (`3DSLICER-ID` actor). Requires **3D Slicer** and an open DICOM database, but **not** the Slicer Hub module.

```bash
cd HubInterface
Slicer --python-script image_display/run_image_display.py -- --local --topic USER-1
```

Flags: `--local`, `--topic`, `--hub`, `--product-name`.

---

## python_client/

Shared Python wire-protocol client (`HubClient`, `SlicerHubClient`).

```bash
cd HubInterface
pip install -e python_client
```

```python
from hub_client import SlicerHubClient, HubConfig, SessionConfig
```

---

## Browser image display

VolView and OHIF ship in the hub deploy zip (see `HubInterface/hub/make_zip.py`). For local dev, use vtk-js / VolView builds against the same hub port.
