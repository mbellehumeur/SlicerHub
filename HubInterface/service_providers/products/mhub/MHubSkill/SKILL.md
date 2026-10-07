---
name: mhub-segmentation
description: Discover MHub medical imaging AI models, look up anatomical segment codes (SegDB/SNOMED), and generate workflow configurations for running models on NIfTI files. Use this skill when users ask about medical image segmentation models, what models can segment specific anatomy, how to run MHub models on their data, or need DICOM-SEG metadata. Works offline with cached data.
---

# MHub Segmentation Skill

This skill provides tools for working with MHub medical imaging AI models and the SegDB anatomical segment database.

## Capabilities

1. **Model Discovery** - Find models by modality, anatomy, or capability
2. **Segment Lookup** - Get SNOMED codes and metadata for anatomical structures
3. **Workflow Generation** - Create custom configs for NIfTI/NRRD input
4. **DCMQI Config Generation** - Generate metadata for DICOM-SEG conversion

## Quick Reference

### Find Models

```bash
# List all models
python scripts/mhub_helper.py models

# Filter by modality
python scripts/mhub_helper.py models --modality CT
python scripts/mhub_helper.py models --modality MR

# Find models that segment specific anatomy
python scripts/mhub_helper.py find liver kidney
python scripts/mhub_helper.py find heart cardiac
python scripts/mhub_helper.py find lung

# Get model details
python scripts/mhub_helper.py model totalsegmentator
python scripts/mhub_helper.py model lungmask
```

### Look Up Segments

```bash
# Search segments
python scripts/mhub_helper.py segments --search kidney
python scripts/mhub_helper.py segments --search heart

# Get segment details (SNOMED code, color)
python scripts/mhub_helper.py segment LIVER
python scripts/mhub_helper.py segment LEFT_KIDNEY
```

### Generate Workflow Configs

```bash
# Generate NIfTI workflow for a model
python scripts/mhub_helper.py config totalsegmentator --pattern flat --output custom.yml
python scripts/mhub_helper.py config lungmask --pattern subject_folders --output custom.yml
python scripts/mhub_helper.py config platipy --pattern bids --modality ct --output custom.yml

# Generate DCMQI config for DICOM-SEG
python scripts/mhub_helper.py dcmqi totalsegmentator --output dcmqi_meta.json
```

### Scaffold Model Packages

```bash
python scripts/mhub_helper.py scaffold example_model \
   --label "Example Model" \
   --description "Basic segmentation package" \
   --modalities CT,MR \
   --primary-modality CT
```

Creates [models/example_model](models/example_model/) with `meta.json`, `config/default.yml`, `Dockerfile`, and `README.md` copied from [assets/model-templates](assets/model-templates) so you can start customizing the package immediately.

### Build Model Containers

```bash
python scripts/mhub_helper.py build example_model \
   --tag mhubai/example_model:latest \
   --build-arg VERSION=local_build
```

Runs `docker build` in `models/example_model/` using the scaffolded Dockerfile, tagging it `mhubai/example_model:latest` by default and letting you supply `--build-arg` or `--platform` overrides.

### Repository Root Overrides

If the skill lives outside the workspace (e.g., in `.github/skills`, `.claude/skills`, or a global install), point it at the real repo root with the top-level `--repo-root /path/to/repo` flag or the `MHUB_SEGMENTATION_REPO_ROOT` environment variable. This ensures `scaffold` and `build` treat `models/` and the rest of the repo consistently no matter where the helper script is executed. The flag takes precedence over the environment variable; omit both to use the default heuristic.

### Run Models via Docker

```bash
# Run a model on your NIfTI folder with an optional workflow or config override
python scripts/mhub_helper.py run lungmask \  
   --input /path/to/nifti \  
   --output /path/to/results \  
   --config ./custom.yml
```

If you prefer to use one of the built-in workflows, swap `--config` for `--workflow default` (or the workflow name from `assets/workflow-templates`).

## Data Cache

This skill includes cached data for offline operation:

| Cache | Contents | Location |
|-------|----------|----------|
| Models | 30 MHub models with metadata | `data/models_summary.json` |
| SegDB | 155 anatomical segments with SNOMED | `data/segdb_cache.json` |
| Configs | Default workflows for all models | `assets/workflow-templates/defaults/` |

**Cache date:** 2025-01-29

To refresh cache (requires network):
```bash
python scripts/mhub_helper.py refresh
```

## Common Tasks

### "What models can segment the liver?"

```bash
python scripts/mhub_helper.py find liver
```

Returns: totalsegmentator, nnunet_liver, bamf_nnunet_ct_liver, mrsegmentator, etc.

### "How do I run TotalSegmentator on my NIfTI files?"

1. Generate a custom workflow config:
   ```bash
   python scripts/mhub_helper.py config totalsegmentator --pattern flat --output custom.yml
   ```

2. Run with Docker:
   ```bash
   docker run --rm --gpus all \
     -v /path/to/nifti:/app/data/input_data:ro \
     -v /path/to/output:/app/data/output_data \
     -v ./custom.yml:/app/config/custom.yml:ro \
     mhubai/totalsegmentator:latest \
     --config /app/config/custom.yml
   ```

For detailed NIfTI workflow instructions, see `references/nifti-workflows.md`.

### "What's the SNOMED code for left kidney?"

```bash
python scripts/mhub_helper.py segment LEFT_KIDNEY
```

Returns: SNOMED code 64033007, color RGB(212, 126, 151)

### "I need to create a DICOM-SEG from my segmentations"

1. Generate DCMQI metadata:
   ```bash
   python scripts/mhub_helper.py dcmqi totalsegmentator --output meta.json
   ```

2. Run DCMQI converter:
   ```bash
   itkimage2segimage \
     --inputImageList segmentation.nii.gz \
     --inputDICOMDirectory /path/to/dicom \
     --outputDICOM output.seg.dcm \
     --inputMetadata meta.json
   ```

## File Organization Patterns

The skill supports three common file organization patterns:

| Pattern | Description | Example Structure |
|---------|-------------|-------------------|
| `flat` | All NIfTI in one folder | `input_data/*.nii.gz` |
| `subject_folders` | One folder per subject | `input_data/subject_id/*.nii.gz` |
| `bids` | BIDS-compliant | `input_data/sub-XX/anat/*_T1w.nii.gz` |

For advanced patterns (clinical trials, multi-site, custom naming), see:
- `references/filestructure-patterns.md` - FileStructureImporter syntax
- `references/dataorganizer-patterns.md` - Output organization options
- `references/nifti-workflows.md` - Complete workflow guide

## Workflow Templates

Pre-built templates in `assets/workflow-templates/`:

| Template | Use Case |
|----------|----------|
| `nifti_generic.yml` | Starting point for any model |
| `bids_template.yml` | BIDS-compliant data |
| `clinical_trial_template.yml` | Multi-site with encoded filenames |
| `defaults/<model>.yml` | Original DICOM configs for reference |

## Available Models (30)

### CT Segmentation
- **totalsegmentator** - 104 structures (organs, bones, muscles, vessels)
- **platipy** - 17 cardiac structures for radiotherapy
- **lungmask** - Lungs and 5 lobes
- **casust** - 8 cardiac structures
- **nnunet_liver** - Liver + tumor
- **nnunet_pancreas** - Pancreas + tumor
- **bamf_nnunet_ct_kidney** - Kidney + tumor + cyst

### MR Segmentation
- **mrsegmentator** - 38 structures (CT/MR compatible)
- **bamf_nnunet_mr_prostate** - Prostate
- **monai_prostate158** - Prostate zones
- **gc_spider_baseline** - 48 spine structures

### PET/CT
- **bamf_pet_ct_lung_tumor** - Lung + FDG-avid tumor
- **bamf_pet_ct_breast_tumor** - Breast FDG-avid tumor

### Prediction Models
- **gc_picai_baseline** - Prostate cancer likelihood
- **gc_grt123_lung_cancer** - Lung cancer risk
- **pyradiomics** - Radiomic feature extraction

Use `python scripts/mhub_helper.py models` for the complete list.

## Dependencies

**For offline use:** No dependencies (uses cached data)

**For cache refresh:** `pip install requests`

**For SegDB Python API:** `pip install segdb`

## Environment Notes

- **Claude.ai (restricted network):** Full functionality using cached data
- **Claude Code:** Full functionality + cache refresh capability
- **Local execution:** Full functionality + Docker for running models
