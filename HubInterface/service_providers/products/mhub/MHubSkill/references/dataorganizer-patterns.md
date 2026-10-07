# DataOrganizer Quick Reference

The DataOrganizer module controls how MHub outputs files to the output directory.

## Target Syntax

```yaml
DataOrganizer:
  targets:
    - <query>--><output_path>
```

## Query Patterns (Left Side)

| Pattern | Matches |
|---------|---------|
| `nifti:mod=seg` | NIfTI segmentation files |
| `nifti:mod=ct` | NIfTI CT images |
| `nifti:mod=seg:roi=LIVER` | Specific ROI |
| `dicomseg:mod=seg` | DICOM-SEG files |
| `log` | Log files |
| `json` | JSON files |
| `*` | All files |

## Path Placeholders (Right Side)

| Placeholder | Description | Example Value |
|-------------|-------------|---------------|
| `[i:sid]` | Instance attribute 'sid' | "PAT001" |
| `[i:patient]` | Instance attribute 'patient' | "subject_42" |
| `[d:roi]` | SegDB ROI identifier | "LIVER" |
| `[basename]` | Original filename with extension | "scan.nii.gz" |
| `[filename]` | Filename without extension | "scan" |
| `[filext]` | File extension | ".nii.gz" |

## Common Output Patterns

### Organized by Subject with Named Segments
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
└── PAT001/
    ├── segmentations/
    │   ├── LIVER.nii.gz
    │   ├── SPLEEN.nii.gz
    │   └── LEFT_KIDNEY.nii.gz
    ├── input/
    │   └── PAT001.nii.gz
    └── logs/
        └── conversion.log
```

### Flat with Subject Prefix
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:sid]_[d:roi].nii.gz
```

Output:
```
output_data/
├── PAT001_LIVER.nii.gz
├── PAT001_SPLEEN.nii.gz
├── PAT002_LIVER.nii.gz
└── PAT002_SPLEEN.nii.gz
```

### BIDS-like Derivatives
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->derivatives/segmentations/sub-[i:subject]/ses-[i:session]/[d:roi].nii.gz
```

### Multi-site Organization
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:site]/[i:patient]/[i:timepoint]/[d:roi].nii.gz
```

### Keep Original Names in Organized Structure
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:sid]/raw/[basename]
    - nifti:mod=seg-->[i:sid]/named/[d:roi].nii.gz
```

### Include Metadata Reports
```yaml
DataOrganizer:
  targets:
    - nifti:mod=seg-->[i:sid]/[d:roi].nii.gz
    - json:mod=report-->[i:sid]/analysis_report.json
```
