# Binary transfer filename policy

The hub enforces an **allowlist** on `resource.fileName` and filters out double extensions when it accepts **binary payload bytes**.

## Default allowed suffixes

Longest match wins (so `study.nii.gz` uses `.nii.gz`, not `.gz`).

| Imaging | Archives / compression |
|---------|-------------------------|
| `.dcm`, `.dicom`, `.dic` | `.zip` |
| `.nii`, `.nii.gz`, `.nrrd` | `.tar`, `.tar.gz`, `.gz` |
| `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp` | |

## Double extensions

After the outer allowlisted suffix is matched, any **earlier** dotted segment
must not be a dangerous type (executables, scripts, installers, etc.).

Examples:

| Filename | Result |
|----------|--------|
| `patient.dcm` | Allowed |
| `volume.nii.gz` | Allowed |
| `bundle.tar.gz` | Allowed |
| `study.dcm.exe` | Rejected (`double_extension`) |
| `malware.exe.dcm` | Rejected |
| `../etc/passwd.dcm` | Rejected (`invalid_file_name`) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HUB_FILENAME_POLICY` | on | Set to `off` to disable checks (dev only). |
| `HUB_ALLOWED_EXTENSIONS` | (see table) | Comma-separated list, e.g. `.dcm,.nii.gz,.zip` |
