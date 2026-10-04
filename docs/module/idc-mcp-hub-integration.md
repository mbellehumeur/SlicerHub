# IDC MCP server — Hub integration gaps

This document summarizes what the hosted IDC MCP server provides today, what Hub
needs to drop hub-side `idc-index`, and the concrete MCP tool to add.

**Hosted endpoint (reference):** `https://api.imaging.datacommons.cancer.gov/mcp`  
**Server info (as of probe):** IDC (Imaging Data Commons) v3 beta

---

## Tools available in the current MCP implementation

| Tool | Purpose |
|------|---------|
| `get_idc_version` | IDC data release and pinned index version |
| `get_stats` | Headline totals (collections, patients, studies, series, size TB) |
| `list_collections` | All IDC collections with cancer types, subjects, etc. |
| `get_collection` | Detailed metadata for one `collection_id` |
| `list_analysis_results` | Derived datasets (segmentations, radiomics, etc.) |
| `list_attributes` | Filterable cohort attributes (call before `build_cohort`) |
| `get_attribute_values` | Distinct values + counts for a categorical attribute |
| `list_tables` | SQL tables (`index`, modality indices, collections, etc.) |
| `get_table_schema` | Column names/types for a table |
| `list_clinical_tables` | Clinical tables available for cohort join |
| `get_clinical_table_schema` | Schema for a clinical table |
| `get_clinical_table` | Rows from a clinical table (filtered) |
| `build_cohort` | Structured filters → counts, sample series, download hints |
| `run_sql` | Read-only DuckDB SQL against the IDC index |
| `get_cohort_urls` | Public `s3://…/*` or `gs://…/*` **prefix per series** (CLI/s5cmd) |
| `get_viewer_url` | OHIF v3 or SLIM browser viewer URL for a series/study |
| `get_citations` | Publications to cite for a cohort |
| `get_licenses` | License breakdown (CC-BY vs CC-BY-NC, etc.) |
| `download_cohort` | Download DICOM to a **local directory** (local MCP only; uses idc-index/s5cmd) |

**Not exposed:** `get_series_file_urls` (or equivalent) — see below.

---

## Why Hub still needed hub `idc-index`

Hub uses IDC in three layers:

1. **Metadata search** — cohort/SQL/detail → **MCP covers this** (`build_cohort`, `run_sql`).
2. **Viewer open (DICOMweb)** — `studyInstanceUID` + `dicomwebRoot` → **MCP + client cover this**; no idc-index required for open alone.
3. **Per-instance byte URLs** — worklist download, direct S3 open, optional `files[]` on open → **requires `IDCClient.get_series_file_URLs()` behavior**, which hub `idc_index_service.py` implemented via idc-index + s5cmd.

The HubClient example MCP **add to worklist** path calls `POST /api/hub/idc/series-files` to expand a series into per-instance HTTPS URLs before saving the worklist entry.

---

## What existing MCP tools do *not* provide

| Tool | Returns | Gap |
|------|---------|-----|
| `run_sql` / `build_cohort` | `series_aws_url: "s3://bucket/uuid/*"` | Series **prefix** only |
| `get_cohort_urls` | One `s3://…/*` or `gs://…/*` per series | Wildcard for **s5cmd/idc CLI**, not a file list |
| `get_viewer_url` | OHIF/SLIM link | Viewing, not downloadable bytes |
| `download_cohort` | Local filesystem | **Not available on Cloud Run**; browser clients cannot use it |

**Listing objects under the prefix and returning anonymous HTTPS URLs per instance is the missing step.**

---

## Required MCP addition: `get_series_file_urls`

Implement the same contract as **`IDCClient.get_series_file_URLs()`** in [idc-index](https://github.com/ImagingDataCommons/idc-index).

### Input

```json
{
  "series_instance_uid": "1.2.840.…",
  "source": "aws",
  "max_instances": 300
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `series_instance_uid` | yes | DICOM Series Instance UID |
| `source` | no | `"aws"` (default) or `"gcs"` |
| `max_instances` | no | Default **300** (match Slicer hub `HUB_IDC_MAX_SLICES`) |

Optional aliases for idc-index parity: `SeriesInstanceUID`, `source_bucket_location`.

### Output

```json
{
  "series_instance_uid": "1.2.840.…",
  "source": "aws",
  "instance_count": 133,
  "files": [
    {
      "url": "https://idc-open-data.s3.amazonaws.com/{crdc-uuid}/{filename}.dcm",
      "fileName": "{filename}.dcm"
    }
  ]
}
```

### Server-side algorithm (match idc-index)

1. **Index lookup** — DuckDB on IDC parquet: `aws_bucket`, `series_aws_url` for `SeriesInstanceUID`.
2. **Prefix** — Derive CRDC series UUID / S3 prefix from `series_aws_url`.
3. **Bucket list** — `s5cmd ls --no-sign-request` (AWS) or GCS equivalent when `source=gcs` (include idc-index GCP bucket remaps if applicable).
4. **Filter** — Keep entries ending in `.dcm`; optional stable sort.
5. **HTTPS** — Map each `s3://` / `gs://` to anonymous HTTPS (IDC direct-loading rules).

Reference implementation: `hub/idc_index_service.py` (`resolve_study_series_files`, `_get_series_file_urls`).

### Errors

- Series not in index → clear 404-style message.
- Instance count > `max_instances` → 422 with count in message.
- s5cmd / listing unavailable → 503.

### Optional follow-up

- **`get_instance_file_url`** — single-instance SM/WSI (idc-index `get_instance_file_URL`); needed for some Slim/direct paths, not general radiology CT/MR.

---

## Hosted MCP operational requirements

`download_cohort` is local-only because listing requires **idc-index + s5cmd on the server**. For Cloud Run:

- **Ship `s5cmd`** in the MCP container (idc-index already depends on it).
- Reuse existing **idc-index / DuckDB** stack (same as `run_sql`).
- **Cache** listings by `(source, series_instance_uid)` — listing can take seconds per series.
- Enforce **max_instances** (default 300) to bound latency and payload size.

Optional: extend `get_cohort_urls` with `expand: true` to return `files[]` instead of `/*` prefixes; a dedicated tool is clearer and matches idc-index API.

---

## Why DICOMweb QIDO/WADO is not a substitute

A browser-side fallback (IDC proxy QIDO → WADO URLs) is **not** equivalent to `get_series_file_URLs`:

- Returns **proxy WADO** URLs, not direct **S3/GCS HTTPS**.
- **CORS** depends on the embedding app origin (e.g. OHIF `:3000` vs HubClient `:9999`).
- Pre-baked Hub worklists and hub idc-index used **S3 HTTPS**, which the download/zip path expects.

For Hub parity with the legacy hub path, MCP should return **S3/GCS HTTPS per file**.

---

## Summary for MCP maintainers

> Add **`get_series_file_urls(series_instance_uid, source='aws'|'gcs', max_instances=300)`** using the same logic as `IDCClient.get_series_file_URLs()`: index lookup → anonymous bucket list via s5cmd → `{ files: [{ url, fileName }] }`. Run on the hosted server with caching and instance caps. Until then, Hub cannot fully replace idc-index for download and direct-URL flows; `get_cohort_urls` only supplies CLI-oriented `s3://…/*` prefixes.

---

## Hub client references

- MCP client: `vtk-js/Sources/IO/Core/HubClient/example/idc-mcp-client.js` — `fetchHubIdcSeriesFiles()` calls the Slicer hub today.
- Hub: `hub/idc_index_service.py` — `resolve_study_series_files()`.
- Pre-baked worklist shape: `vtk-js/Sources/IO/Core/HubClient/example/idc-data/idc-portal-demo-series-1.json` (S3 HTTPS `files[]`).
