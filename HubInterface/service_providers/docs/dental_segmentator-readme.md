# Dental Segmentator Hub service provider

Standalone Python service provider for Dental Segmentator — fully automatic CT/CBCT
dental segmentation (maxilla, mandible, upper/lower teeth, mandibular canal).

Upstream: [gaudot/SlicerDentalSegmentator](https://github.com/gaudot/SlicerDentalSegmentator).
Kitware write-up: [kitware.com/dental-segmentator](https://www.kitware.com/dental-segmentator/).
Pretrained nnU-Net weights: [Zenodo](https://zenodo.org/doi/10.5281/zenodo.10829674) /
[GitHub releases](https://github.com/gaudot/SlicerDentalSegmentator/releases).

On **`nifti-send`**, the server stages the volume as `{case}_0000.nii.gz` and runs:

`nnUNetv2_predict_from_modelfolder -i … -o … -m <model> -f 0`

then publishes the labelmap back with **`nifti-send`**.

**`dicom-send`** is not implemented yet (convert to NIfTI / use nifti-send).

Framework: `service_provider.py` (shared with other service providers).

## Dependencies

```bash
pip install aiohttp nnunetv2 torch
```

CUDA recommended; CPU works but is slow (set `DENTAL_SEG_DEVICE=cpu`).

## Model weights

By default the service provider **auto-downloads** [Dataset111_453CT_v100.zip](https://github.com/gaudot/SlicerDentalSegmentator/releases/download/v1.0.0-alpha/Dataset111_453CT_v100.zip) (~220 MB) on the first job and caches it under:

`~/.slicer-hub/models/dental_segmentator`

Optional override — point at a pre-unzipped folder that contains `dataset.json` and/or `fold_0` (Git Bash rewrites `/path/...`; use a real Windows path):

```bash
# Git Bash / MSYS — quote a drive path:
export DENTAL_SEG_MODEL_PATH='C:/Users/marti/models/dental'
# optional: cuda | cpu | mps
export DENTAL_SEG_DEVICE=cuda
```

PowerShell:

```powershell
$env:DENTAL_SEG_MODEL_PATH = "C:\Users\marti\models\dental"
$env:DENTAL_SEG_DEVICE = "cuda"
```

## Slicer Hub setup

In **Service Providers**, add or edit a row:

| Field | Value |
|-------|--------|
| Product | `DENTAL_SEG` |
| Version | `1.0` |
| Description | e.g. dental CT/CBCT segmentation |
| Hub | `SLICER-HUB` or `SLICER-HUB-CLOUD` |
| onMessage script | `service_providers/products/dental_segmentator.py` |

Click **Connect**. Subscribed events: `dicom-send`, `nifti-send`, `status-request`.

## Run standalone (no Slicer UI)

From `HubInterface/`:

```bash
# optional: DENTAL_SEG_MODEL_PATH / DENTAL_SEG_DEVICE
python service_providers/products/dental_segmentator.py --local
```

Default hub is **SLICER-HUB-CLOUD**; `--local` uses `http://127.0.0.1:2018`.

## End-to-end job flow (nifti-send)

```
VolView / IRA                       Hub                         dental_segmentator.py
     |                               |                                  |
     |  nifti-send (.nii.gz)         |                                  |
     |------------------------------>|--------------------------------->|
     |                               |     download → input_dir         |
     |  status-update                |<---------------------------------|
     |                               |     nnUNetv2_predict_from_modelfolder
     |  status-update (progress)     |<---------------------------------|
     |  nifti-send (labelmap)        |<---------------------------------|
     |  status-update (complete)     |<---------------------------------|
```

Cite when using results:

> Dot G, et al. DentalSegmentator: robust open source deep learning-based CT and CBCT image segmentation. Journal of Dentistry (2024) doi:10.1016/j.jdent.2024.105130
