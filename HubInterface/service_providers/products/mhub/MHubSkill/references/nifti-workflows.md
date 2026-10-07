# Running MHub Models on NIfTI Files

MHub models are containerized AI pipelines that, by default, accept DICOM input and produce DICOM-SEG output. This guide explains how to create custom workflow configurations to run any MHub model on NIfTI (or NRRD) files stored locally.

## Overview

The adaptation process involves:
1. Replacing the `DicomImporter` with `FileStructureImporter`
2. Configuring the importer to match your file organization
3. Adjusting the output via `DataOrganizer`
4. Optionally removing the `DsegConverter` (which requires DICOM reference)

## Quick Start

For users who want to get started immediately with a simple flat directory of NIfTI files:

```bash
# 1. Create the custom config
cat > custom_nifti.yml << 'EOF'
general:
  data_base_dir: /app/data
  version: 1.0
  description: Run model on NIfTI files (flat directory)

execute:
- FileStructureImporter
- NiftiConverter
- TotalSegmentatorMLRunner  # Replace with your model's runner
- DataOrganizer

modules:
  FileStructureImporter:
    import_id: sid
    structures:
      - re:(.+)\.nii(\.gz)?$::$sid@instance@nifti:mod=ct

  DataOrganizer:
    targets:
    - nifti:mod=seg-->[i:sid]/segmentations/[d:roi].nii.gz
    - nifti:mod=ct-->[i:sid]/input/[basename]
EOF

# 2. Run the model
docker run --rm --gpus all \
  -v /path/to/your/nifti/files:/app/data/input_data:ro \
  -v /path/to/output:/app/data/output_data \
  -v $(pwd)/custom_nifti.yml:/app/config/custom.yml:ro \
  mhubai/totalsegmentator:latest \
  --config /app/config/custom.yml
```

---

## Understanding the Configuration

### The Default DICOM Workflow

Every MHub model has a default workflow that looks like this:

```yaml
execute:
- DicomImporter        # Import DICOM files
- NiftiConverter       # Convert to NIfTI for the model
- <ModelRunner>        # Run the AI model
- DsegConverter        # Convert output to DICOM-SEG
- DataOrganizer        # Organize output files
```

### The NIfTI Workflow

For NIfTI input, we modify it to:

```yaml
execute:
- FileStructureImporter  # Import NIfTI files directly
- NiftiConverter         # Pass-through (handles NRRD→NIfTI if needed)
- <ModelRunner>          # Run the AI model  
- DataOrganizer          # Organize output files
```

Note: We remove `DsegConverter` because DICOM-SEG requires a reference DICOM series.

---

## Extracting Metadata from Filenames and Folders

The `FileStructureImporter` can extract metadata embedded in your file and folder names. This metadata flows through the pipeline and can be used for organizing outputs, filtering cases, and generating reports.

### Metadata Extraction Syntax

#### 1. Placeholders (`$variable`)

Capture folder or file names into metadata variables:

```yaml
structures:
  - $patient/$session/$modality.nii.gz@instance@nifti
```

For path `input_data/PAT001/SES01/t1w.nii.gz`, this captures:
- `patient` = "PAT001"
- `session` = "SES01"
- `modality` = "t1w"

#### 2. Regex with Named Groups (`re:pattern::$var1::$var2`)

Extract parts of filenames using regex capture groups:

```yaml
structures:
  - re:([A-Z]+)(\d+)_(\w+)\.nii\.gz$::$site::$id::$sequence@instance@nifti:mod=mr
```

For file `MGH042_FLAIR.nii.gz`, this captures:
- `site` = "MGH"
- `id` = "042"
- `sequence` = "FLAIR"

The capture groups map to variables in order after the `::` separators.

#### 3. Combined Placeholders with Literal Filters

Mix literal text with placeholders to match structured naming conventions:

```yaml
structures:
  - sub-$subject@instance/ses-$session/anat/re:.*_(.+)\.nii\.gz$::$suffix@nifti:mod=mr
```

For `sub-001/ses-baseline/anat/sub-001_ses-baseline_T1w.nii.gz`:
- `subject` = "001" (from `sub-$subject`)
- `session` = "baseline" (from `ses-$session`)
- `suffix` = "T1w" (from regex)

#### 4. Static Metadata on Import

Add fixed metadata values using the `:key=value` syntax:

```yaml
structures:
  - ct/$patient@instance@nifti:mod=ct:bodypart=chest
  - mr/$patient@instance@nifti:mod=mr:bodypart=brain
```

#### 5. CSV Metadata Extension

Load additional metadata from a CSV file keyed by an extracted variable:

```yaml
FileStructureImporter:
  import_id: patient
  structures:
    - $patient@instance/image.nii.gz@nifti:mod=ct
  meta:
    - type: csv
      path: /app/data/input_data/metadata.csv
      id: patient
```

With `metadata.csv`:
```csv
patient,age,sex,diagnosis
PAT001,65,M,lung_cancer
PAT002,42,F,healthy
```

The CSV fields (`age`, `sex`, `diagnosis`) become available as metadata for each instance.

---

### Using Extracted Metadata

Once extracted, metadata can be used throughout the pipeline:

**1. Composite instance identification:**
```yaml
import_id: site/patient/session  # Unique ID from multiple variables
```

**2. Output path organization:**
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:site]/[i:patient]/[i:session]/[d:roi].nii.gz
```

**3. Filtering instances:**
```yaml
# Add AttributeFilter to the execute list
execute:
  - FileStructureImporter
  - AttributeFilter
  - NiftiConverter
  - ...

modules:
  AttributeFilter:
    instance_attributes:
      site: MGH
      timepoint: baseline
```

**4. Report generation:**
```yaml
# Add ReportExporter to the execute list
ReportExporter:
  format: compact
  includes:
    - attr: patient
      label: PatientID
    - attr: diagnosis
      label: ClinicalDiagnosis
    - attr: age
      label: PatientAge
```

---

### Clinical Trial Example

Input structure with embedded metadata:
```
input_data/
├── SITE01_PAT001_BL_CT.nii.gz
├── SITE01_PAT001_FU_CT.nii.gz
├── SITE01_PAT002_BL_CT.nii.gz
├── SITE02_PAT001_BL_CT.nii.gz
```

Configuration extracting site, patient, timepoint, and modality:
```yaml
FileStructureImporter:
  import_id: site/patient/timepoint
  structures:
    - re:([A-Z]+\d+)_([A-Z]+\d+)_([A-Z]+)_(\w+)\.nii\.gz$::$site::$patient::$timepoint::$modality@instance@nifti:mod=ct

DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:site]/[i:patient]/[i:timepoint]/segmentations/[d:roi].nii.gz
```

Output organized by extracted metadata:
```
output_data/
├── SITE01/
│   ├── PAT001/
│   │   ├── BL/
│   │   │   └── segmentations/
│   │   │       ├── LIVER.nii.gz
│   │   │       └── SPLEEN.nii.gz
│   │   └── FU/
│   │       └── segmentations/
│   │           └── ...
│   └── PAT002/
│       └── BL/
│           └── segmentations/
│               └── ...
└── SITE02/
    └── ...
```

---

## File Organization Patterns

The `FileStructureImporter` uses a pattern-matching syntax to understand your file organization. Here are configurations for common scenarios:

### Pattern 1: Flat Directory

All NIfTI files in a single folder, each file is one case:

```
input_data/
├── patient001.nii.gz
├── patient002.nii.gz
└── scan_abc.nii.gz
```

**Configuration:**
```yaml
FileStructureImporter:
  import_id: sid
  structures:
    - re:(.+)\.nii(\.gz)?$::$sid@instance@nifti:mod=ct
```

**Explanation:**
- `re:(.+)\.nii(\.gz)?$` — Regex matching any `.nii` or `.nii.gz` file
- `::$sid` — Capture the filename (without extension) into the `sid` variable
- `@instance` — Treat each file as a separate processing instance
- `@nifti:mod=ct` — Import as NIfTI with modality=CT

---

### Pattern 2: Subject Folders

Each subject has their own folder containing one or more images:

```
input_data/
├── sub-001/
│   └── image.nii.gz
├── sub-002/
│   └── image.nii.gz
└── sub-003/
│   └── image.nii.gz
```

**Configuration:**
```yaml
FileStructureImporter:
  import_id: subject
  structures:
    - $subject@instance/re:.*\.nii(\.gz)?$@nifti:mod=ct
```

**Explanation:**
- `$subject` — Wildcard capturing the folder name into `subject`
- `@instance` — The subject folder defines an instance
- Second part matches any NIfTI file inside

---

### Pattern 3: BIDS-like Structure

Following Brain Imaging Data Structure conventions:

```
input_data/
├── sub-001/
│   └── anat/
│       └── sub-001_T1w.nii.gz
├── sub-002/
│   └── anat/
│       └── sub-002_T1w.nii.gz
```

**Configuration:**
```yaml
FileStructureImporter:
  import_id: subject
  structures:
    - $subject@instance/anat/re:.*_T1w\.nii(\.gz)?$@nifti:mod=mr
  excludes:
    - derivatives/
    - sourcedata/
```

**Explanation:**
- Matches BIDS `sub-XXX/anat/*_T1w.nii.gz` pattern
- Sets modality to MR
- Excludes common BIDS directories that shouldn't be processed

---

### Pattern 4: Multiple Modalities per Subject

Subject folders with multiple image types:

```
input_data/
├── patient_A/
│   ├── ct.nii.gz
│   └── pet.nii.gz
├── patient_B/
│   ├── ct.nii.gz
│   └── pet.nii.gz
```

**Configuration:**
```yaml
FileStructureImporter:
  import_id: patient
  structures:
    - $patient@instance/ct.nii.gz@nifti:mod=ct
    - $patient/pet.nii.gz@nifti:mod=pt
```

**Explanation:**
- First structure creates instance and imports CT
- Second structure imports PET into the same instance (note: no `@instance`)

---

### Pattern 5: Study/Series Hierarchy

Hospital-like organization with study and series levels:

```
input_data/
├── STUDY_001/
│   ├── series_ct/
│   │   └── image.nii.gz
│   └── series_pet/
│       └── image.nii.gz
├── STUDY_002/
│   └── series_ct/
│       └── image.nii.gz
```

**Configuration:**
```yaml
FileStructureImporter:
  import_id: study/series
  structures:
    - $study@instance/$series@bundle/re:.*\.nii(\.gz)?$@nifti:mod=ct
```

**Explanation:**
- `import_id: study/series` — Composite identifier
- `@bundle` — Groups files within a series together

---

## Output Organization

The `DataOrganizer` module controls how results are saved. Key placeholders:

| Placeholder | Description |
|-------------|-------------|
| `[i:sid]` | Instance attribute (e.g., subject ID) |
| `[d:roi]` | SegDB region of interest ID |
| `[basename]` | Original filename |
| `[filename]` | Filename without extension |

### Common Output Patterns

**Organized by subject with named segments:**
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:sid]/segmentations/[d:roi].nii.gz
    - nifti:mod=ct-->[i:sid]/input/[basename]
    - log-->[i:sid]/logs/[basename]
```

Output:
```
output_data/
├── patient001/
│   ├── segmentations/
│   │   ├── LIVER.nii.gz
│   │   ├── SPLEEN.nii.gz
│   │   └── ...
│   ├── input/
│   │   └── patient001.nii.gz
│   └── logs/
│       └── plastimatch.log
```

**Flat output with subject prefix:**
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:sid]_[d:roi].nii.gz
```

Output:
```
output_data/
├── patient001_LIVER.nii.gz
├── patient001_SPLEEN.nii.gz
├── patient002_LIVER.nii.gz
└── ...
```

---

## Complete Examples

### Example 1: TotalSegmentator on Flat NIfTI Directory

```yaml
general:
  data_base_dir: /app/data
  version: 1.0
  description: TotalSegmentator on NIfTI files

execute:
- FileStructureImporter
- NiftiConverter
- TotalSegmentatorMLRunner
- DataOrganizer

modules:
  FileStructureImporter:
    import_id: sid
    structures:
      - re:(.+)\.nii(\.gz)?$::$sid@instance@nifti:mod=ct

  TotalSegmentatorMLRunner:
    use_fast_mode: false

  DataOrganizer:
    targets:
    - nifti:mod=seg-->[i:sid]/[d:roi].nii.gz
    - log-->[i:sid]/log/[basename]
```

### Example 2: Lung Segmentation on NRRD Files

```yaml
general:
  data_base_dir: /app/data
  version: 1.0
  description: LungMask on NRRD files

execute:
- FileStructureImporter
- NiftiConverter
- LungMaskRunner
- DataOrganizer

modules:
  FileStructureImporter:
    import_id: caseId
    structures:
      - re:(.+)\.nrrd$::$caseId@instance@nrrd:mod=ct

  LungMaskRunner:
    batchsize: 64

  DataOrganizer:
    targets:
    - nifti:mod=seg-->[i:caseId]/seg/[d:roi].nii.gz
    - nifti:mod=seg-->[i:caseId]/raw/[basename]
    - nifti:mod=ct-->[i:caseId]/image/[basename]
```

### Example 3: Prostate Segmentation on BIDS MRI Data

```yaml
general:
  data_base_dir: /app/data
  version: 1.0
  description: Prostate segmentation on BIDS T2w MRI

execute:
- FileStructureImporter
- NiftiConverter
- ProstateMRRunner  # hypothetical runner
- DataOrganizer

modules:
  FileStructureImporter:
    import_id: subject
    structures:
      - $subject@instance/anat/re:.*_T2w\.nii(\.gz)?$@nifti:mod=mr
    excludes:
      - derivatives/
      - .bidsignore

  DataOrganizer:
    targets:
    - nifti:mod=seg-->derivatives/segmentations/[i:subject]/[d:roi].nii.gz
```

---

## Interactive Configuration Helper

If you're unsure about your file structure, here's a decision tree:

**Q1: Are all your files in one folder, or organized in subfolders?**
- One folder → Use Pattern 1 (flat directory)
- Subfolders → Continue to Q2

**Q2: Does each subfolder represent one case/subject?**
- Yes → Use Pattern 2 (subject folders)
- No, there's more hierarchy → Continue to Q3

**Q3: Are you using BIDS format?**
- Yes → Use Pattern 3 (BIDS-like)
- No → Use Pattern 4 or 5 depending on your structure

**Q4: Do you have multiple image types (CT+PET, T1+T2, etc.)?**
- Yes → Use Pattern 4 (multiple modalities)
- No → Simpler patterns should work

---

## Discovering Model Runner Names

To find the correct runner module name for a model, check its default config:

```bash
# View a model's default workflow
curl -s "https://raw.githubusercontent.com/MHubAI/models/main/models/MODEL_NAME/config/default.yml"
```

Common runners:
| Model | Runner Module |
|-------|---------------|
| totalsegmentator | TotalSegmentatorMLRunner |
| lungmask | LungMaskRunner |
| platipy | PlatipyRunner |
| nnunet_* | NNUnetRunner |

---

## Troubleshooting

### "0 instances imported"

Your file structure doesn't match the pattern. Try:
1. Check that files exist in the mounted input directory
2. Verify the regex pattern matches your filenames
3. Add `--verbose` flag to see what's being scanned

### "No DICOM reference for DsegConverter"

Remove `DsegConverter` from your workflow — it requires DICOM input to create DICOM-SEG output.

### Model expects different input format

Some models expect specific input. Check the model's runner module or add conversion steps. The `NiftiConverter` handles NRRD→NIfTI automatically.

---

## Running the Container

```bash
docker run --rm --gpus all \
  -v /local/input:/app/data/input_data:ro \
  -v /local/output:/app/data/output_data \
  -v /local/custom.yml:/app/config/custom.yml:ro \
  mhubai/MODEL_NAME:latest \
  --config /app/config/custom.yml
```

**Flags:**
- `--rm` — Remove container after completion
- `--gpus all` — Enable GPU acceleration
- `:ro` — Mount as read-only (recommended for input/config)
- `--config` — Specify custom workflow file

---

## References

- [MHub Documentation](https://github.com/MHubAI/documentation)
- [MHub Models Repository](https://github.com/MHubAI/models)
- [FileStructureImporter Source](https://github.com/MHubAI/mhubio/blob/main/mhubio/modules/importer/FileStructureImporter.py)
- [SegDB Segment Database](https://github.com/MHubAI/SegDB)
- [Official NIfTI Tutorial](https://github.com/MHubAI/documentation/tree/main/tutorials/run_lungmask_on_chestct_in_nifti_format)
