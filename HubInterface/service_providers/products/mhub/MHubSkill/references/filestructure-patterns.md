# FileStructureImporter Quick Reference

## Pattern Syntax

```
path/structure::$var1::$var2@instance@datatype:meta=value
```

| Element | Purpose | Example |
|---------|---------|---------|
| `$variable` | Capture folder/file name | `$patient` captures "PAT001" |
| `re:pattern` | Regex match | `re:(.+)\.nii\.gz$` |
| `::$var` | Map regex group to variable | `::$id::$session` |
| `@instance` | Mark as processing instance | `$subject@instance/...` |
| `@datatype` | Import as data type | `@nifti`, `@dicom`, `@nrrd` |
| `:key=value` | Static metadata | `:mod=ct:bodypart=chest` |

## Common Patterns

### Flat Directory
```yaml
structures:
  - re:(.+)\.nii(\.gz)?$::$sid@instance@nifti:mod=ct
```

### Subject Folders
```yaml
structures:
  - $subject@instance/re:.*\.nii(\.gz)?$@nifti:mod=ct
```

### BIDS Structure
```yaml
structures:
  - sub-$subject@instance/ses-$session/anat/re:.*_T1w\.nii(\.gz)?$@nifti:mod=mr
excludes:
  - derivatives/
  - sourcedata/
```

### Clinical Trial (Encoded Filenames)
```yaml
structures:
  - re:([A-Z]+)_([A-Z0-9]+)_([A-Z]+)_(\w+)\.nii\.gz$::$site::$patient::$timepoint::$modality@instance@nifti:mod=ct
import_id: site/patient/timepoint
```

### Multiple Modalities
```yaml
structures:
  - $patient@instance/ct.nii.gz@nifti:mod=ct
  - $patient/pet.nii.gz@nifti:mod=pt
```

## Metadata Flow

Extracted metadata is available in:

1. **import_id** - Composite instance identifier
   ```yaml
   import_id: site/patient/session
   ```

2. **DataOrganizer** - Output paths
   ```yaml
   targets:
     - nifti:mod=seg-->[i:site]/[i:patient]/[i:session]/[d:roi].nii.gz
   ```

3. **AttributeFilter** - Case selection
   ```yaml
   AttributeFilter:
     instance_attributes:
       site: MGH
   ```

4. **ReportExporter** - Metadata in reports
   ```yaml
   ReportExporter:
     includes:
       - attr: patient
         label: PatientID
   ```

## CSV Metadata Extension

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

CSV format:
```csv
patient,age,sex,diagnosis
PAT001,65,M,lung_cancer
PAT002,42,F,healthy
```
