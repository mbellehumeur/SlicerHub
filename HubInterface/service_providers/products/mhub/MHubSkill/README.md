# MHub Segmentation Skill

A Claude skill for discovering MHub medical imaging AI models, looking up anatomical segment codes, and generating workflow configurations for running models on NIfTI files.

## Features

- **Model Discovery**: Find models by modality (CT, MR, PET), anatomy (liver, lung, heart), or capability
- **Segment Lookup**: Get SNOMED CT codes, display colors, and metadata for 155 anatomical structures
- **Workflow Generation**: Create custom YAML configs to run MHub models on NIfTI/NRRD files
- **DCMQI Config Generation**: Generate metadata JSON for DICOM-SEG conversion
- **Offline-First**: Works without network access using bundled cached data

## Installation

### For Claude.ai

1. Download or clone this skill folder

2. Create a ZIP file of the skill:
   ```bash
   cd mhub-segmentation
   zip -r ../mhub-segmentation.zip .
   ```

3. In Claude.ai:
   - Go to **Settings** → **Features** → **Skills**
   - Click **Add Skill**
   - Upload the `mhub-segmentation.zip` file
   - Enable the skill

4. The skill will automatically activate when you ask about:
   - MHub models or medical image segmentation
   - Running AI models on NIfTI files
   - SNOMED codes for anatomical structures
   - DICOM-SEG conversion

### For Claude Code

1. Clone or copy the skill to your Claude Code skills directory:
   ```bash
   # Option 1: User skills (available in all projects)
   cp -r mhub-segmentation ~/.claude/skills/
   
   # Option 2: Project skills (available in current project)
   cp -r mhub-segmentation .claude/skills/
   ```

2. The skill is now available. Claude Code will automatically use it when relevant, or you can invoke it directly:
   ```
   /mhub-segmentation
   ```

3. To refresh the model cache (requires network):
   ```bash
   python ~/.claude/skills/mhub-segmentation/scripts/mhub_helper.py refresh
   ```

## Usage Examples

### Find Models

Ask Claude:
- "What MHub models can segment the liver?"
- "Show me CT segmentation models"
- "What models work with MRI data?"

Or use the helper script directly:
```bash
python scripts/mhub_helper.py find liver kidney
python scripts/mhub_helper.py models --modality CT
python scripts/mhub_helper.py model totalsegmentator
```

### Run Models on NIfTI Files

Ask Claude:
- "How do I run TotalSegmentator on my NIfTI files?"
- "Generate a workflow config for lungmask with BIDS data"
- "Help me process my CT scans with MHub"

Or generate configs directly:
```bash
python scripts/mhub_helper.py config totalsegmentator --pattern flat --output custom.yml

docker run --rm --gpus all \
  -v /path/to/nifti:/app/data/input_data:ro \
  -v /path/to/output:/app/data/output_data \
  -v ./custom.yml:/app/config/custom.yml:ro \
  mhubai/totalsegmentator:latest \
  --config /app/config/custom.yml
```

### Look Up Segment Codes

Ask Claude:
- "What's the SNOMED code for left kidney?"
- "Show me segment info for LIVER"
- "What segments does TotalSegmentator output?"

Or use the script:
```bash
python scripts/mhub_helper.py segment LEFT_KIDNEY
python scripts/mhub_helper.py segments --search heart
```

## Skill Contents

```
mhub-segmentation/
├── SKILL.md                 # Main skill instructions
├── README.md                # This file
├── data/
│   ├── models_cache.json    # Raw MHub API response
│   ├── models_summary.json  # Processed model index
│   └── segdb_cache.json     # SegDB segments with SNOMED codes
├── references/
│   ├── nifti-workflows.md   # Complete NIfTI workflow guide
│   ├── filestructure-patterns.md  # FileStructureImporter syntax
│   └── dataorganizer-patterns.md  # Output organization options
├── scripts/
│   └── mhub_helper.py       # CLI tool for queries and config generation
└── assets/
    └── workflow-templates/
        ├── nifti_generic.yml        # Generic NIfTI template
        ├── bids_template.yml        # BIDS-compatible template
        ├── clinical_trial_template.yml  # Multi-site template
        └── defaults/                # Original configs for 30 models
```

## Cached Data

The skill includes cached data for offline operation:

| Data | Count | Source | Cache Date |
|------|-------|--------|------------|
| MHub Models | 30 | mhub.ai API | 2025-01-29 |
| SegDB Segments | 155 | segdb Python package | 2025-01-29 |
| Default Configs | 30 | MHub GitHub repository | 2025-01-29 |

To update the cache (requires network access):
```bash
python scripts/mhub_helper.py refresh
```

## Supported Models

The skill includes data for all 30 MHub models:

**CT Segmentation:** totalsegmentator (104 structures), platipy (cardiac), lungmask, casust, nnunet_liver, nnunet_pancreas, bamf_nnunet_ct_kidney, gc_lunglobes, nnunet_segthor, gc_autopet_fpr, gc_nnunet_pancreas, msk_smit_lung_gtv

**MR Segmentation:** mrsegmentator, bamf_nnunet_mr_prostate, bamf_nnunet_mr_liver, nnunet_prostate_zonal_task05, nnunet_prostate_task24, monai_prostate158, gc_spider_baseline

**PET/CT:** bamf_pet_ct_lung_tumor, bamf_pet_ct_breast_tumor

**Prediction:** gc_picai_baseline, gc_grt123_lung_cancer, gc_stoic_baseline, fmcib_radiomics, pyradiomics, gc_node21_baseline, gc_wsi_bgseg, gc_tiger_lb2

## Requirements

- **Offline use:** Python 3.8+ (no additional packages needed)
- **Cache refresh:** `requests` package
- **SegDB Python API:** `segdb` package
- **Running models:** Docker with NVIDIA GPU support

## License

This skill is provided under the MIT License.

MHub models have individual licenses (typically Apache 2.0 for code, CC BY-NC 4.0 for weights). Check each model's license before use.

## Resources

- [MHub Website](https://mhub.ai)
- [MHub Models Repository](https://github.com/MHubAI/models)
- [MHub Documentation](https://github.com/MHubAI/documentation)
- [SegDB Segment Database](https://github.com/MHubAI/SegDB)
- [Agent Skills Specification](https://agentskills.io)
