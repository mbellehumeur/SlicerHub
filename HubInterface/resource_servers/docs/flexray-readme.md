# FleXray Hub product (stub)

Upstream: [FleXray](https://flexray.csail.mit.edu/) — universal clinical X-ray
anatomy segmentation (MIT CSAIL / MGH / HMS). Paper: [arXiv:2609.26756](https://arxiv.org/abs/2609.26756).

> **Status:** scaffold only. `products/flexray/flexray.py` does not run inference
> or subscribe to the hub yet. The worklist exposes FleXray under **Local Inference**
> (info card). Remote Inference / Hub job wiring will come later.

## Paths

| Field | Value |
|-------|--------|
| Product | `FLEXRAY` |
| Info JSON | `resource_servers/products/flexray/flexray.info.json` |
| Script | `resource_servers/products/flexray/flexray.py` |

Sync the info JSON into the hub JS client with:

```bash
# from SlicerHub-js-clients/ (or hub-js-client/)
node tools/sync_inference_info.js
```

## Worklist cases

The Integrated Worklist org filter **FleXray** (`flexray`) is the default list:
20 public IDC CR/DX radiographs (`worklist/flexray-manifest.json`). Clicking the
Evidence Creators **FleXray** button switches to that org. Cases open via IDC direct S3
(`openMode: idc`). CR/DX rows open in OHIF.

The [official browser demo](https://flexray.csail.mit.edu/) is not DICOM: its
`demo_manifest.json` samples are PNGs (**AC joints**, **oblique neck**, **hands
& wrists**). Those files are not on IDC. This worklist instead picks IDC CR/DX
series that cover the same anatomy the demo and paper emphasize (neck/C-spine,
shoulder, chest including pediatric and ribs, abdomen, pelvis/hip, lumbar and
thoracic spine, elbow, knee, femur, skull). **IDC has no public hand/wrist
CR/DX series.**

Rebuild the frozen UID list (requires `idc-index`):

```bash
# from SlicerHub-js-clients/
python packages/worklist/tools/build_flexray_worklist.py
```

| Worklist name | Collection | Modality |
|---------------|------------|----------|
| Chest PA · DX | lidc_idri | DX |
| Chest PA · CR | covid_19_ar | CR |
| Chest AP portable · CR | covid_19_ar | CR |
| Chest AP · DX | midrc_ricord_1c | DX |
| Chest LAT · CR | acrin_nsclc_fdg_pet | CR |
| Ribs oblique · CR | acrin_nsclc_fdg_pet | CR |
| Pediatric chest AP · CR | midrc_ricord_1c | CR |
| Abdomen KUB AP · CR | covid_19_ny_sbu | CR |
| Abdomen erect · DX | cmb_crc | DX |
| Pelvis AP · CR | varepop_apollo | CR |
| Hip AP · CR | varepop_apollo | CR |
| C-spine LAT · DX | varepop_apollo | DX |
| C-spine AP · DX | varepop_apollo | DX |
| L-spine LAT · CR | cmb_mml | CR |
| T-spine LAT · CR | cmb_mml | CR |
| Shoulder AP · DX | varepop_apollo | DX |
| Elbow AP · CR | varepop_apollo | CR |
| Knee AP · CR | varepop_apollo | CR |
| Femur · CR | cmb_mml | CR |
| Skull LAT · CR | cmb_mml | CR |
