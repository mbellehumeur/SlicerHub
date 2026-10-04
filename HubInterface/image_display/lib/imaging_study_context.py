"""FHIRcast ImagingStudy-open context extractors (aligned with vtk-js imagingStudyContext.js)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict
from urllib.parse import urlparse

HUB_OPEN_MODE = "urn:cast:open-mode"
HUB_OPEN_MODE_DICOMWEB = "dicomweb"
HUB_OPEN_MODE_DICOM_URL = "dicom-url"
HUB_OPEN_MODE_FILES = "files"
HUB_OPEN_MODE_IDC = "idc"
HUB_OPEN_MODE_LOCAL_DICOM = "local-dicom"

HUB_IDENTIFIER_DICOM_UID = "urn:dicom:uid"
HUB_IDENTIFIER_NIFTI_URL = "urn:cast:nifti-url"
HUB_IDENTIFIER_NIFTI_FILENAME = "urn:cast:nifti-filename"
HUB_IDENTIFIER_WORKLIST_SAMPLE_ID = "urn:cast:worklist-sample-id"
HUB_IDENTIFIER_VOLVIEW_SAMPLE_ID = "urn:cast:volview-sample-id"
HUB_DICOMWEB_ROOT = "urn:cast:dicomweb-root"
HUB_IDENTIFIER_IDC = "idc"
HUB_IDENTIFIER_IDC_SOURCE_BUCKET = "idc-source-bucket"

_ALLOWED_FILE_URL_SCHEMES = frozenset(("http", "https", "s3", "gs"))


class HubFileEntry(TypedDict, total=False):
    url: str
    fileName: str
    mimeType: str
    role: str
    label: str
    data: Any


class IdcFallbackPlan(TypedDict, total=False):
    study_uid: str
    series_uid: str
    source_bucket: str
    files: List[HubFileEntry]


class ImagingStudyOpenPlan(TypedDict, total=False):
    mode: str
    study_id: str
    files: List[HubFileEntry]
    study_uid: str
    series_uid: str
    dicomweb_root: str
    idc_fallback: IdcFallbackPlan


def _normalize_system(system: Any) -> str:
    return str(system).strip().lower() if isinstance(system, str) else ""


def _normalize_uid(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if trimmed.lower().startswith("urn:oid:"):
        return trimmed[8:].strip()
    return trimmed


def extract_study_context_item(
    context: Any, key: str
) -> Optional[Dict[str, Any]]:
    if not isinstance(context, list):
        return None
    normalized_key = str(key or "").strip().lower()
    for entry in context:
        if not isinstance(entry, dict):
            continue
        entry_key = entry.get("key")
        if not isinstance(entry_key, str):
            continue
        if entry_key.strip().lower() != normalized_key:
            continue
        resource = entry.get("resource")
        if isinstance(resource, dict):
            return resource
    return None


def extract_identifier_value(context: Any, system: str) -> str:
    study_resource = extract_study_context_item(context, "study")
    if not study_resource:
        return ""
    identifiers = study_resource.get("identifier")
    if not isinstance(identifiers, list):
        return ""
    want = _normalize_system(system)
    for identifier in identifiers:
        if not isinstance(identifier, dict):
            continue
        if _normalize_system(identifier.get("system")) != want:
            continue
        value = identifier.get("value")
        if isinstance(value, str):
            return value.strip()
    return ""


def extract_worklist_sample_id(context: Any) -> str:
    return extract_identifier_value(
        context, HUB_IDENTIFIER_VOLVIEW_SAMPLE_ID
    ) or extract_identifier_value(context, HUB_IDENTIFIER_WORKLIST_SAMPLE_ID)


def extract_study_display_id(context: Any) -> str:
    study_resource = extract_study_context_item(context, "study")
    if study_resource:
        study_id = study_resource.get("id")
        if isinstance(study_id, str) and study_id.strip():
            return study_id.strip()
    return extract_worklist_sample_id(context)


def extract_dicom_study_uid(context: Any) -> str:
    study_resource = extract_study_context_item(context, "study")
    if not study_resource:
        return ""
    uid = _normalize_uid(study_resource.get("uid"))
    if uid:
        return uid
    return _normalize_uid(extract_identifier_value(context, HUB_IDENTIFIER_DICOM_UID))


def extract_dicom_series_uid(context: Any) -> str:
    series_resource = extract_study_context_item(context, "series")
    if series_resource:
        return _normalize_uid(series_resource.get("uid"))

    study_resource = extract_study_context_item(context, "study")
    if not study_resource:
        return ""
    series_list = study_resource.get("series")
    if not isinstance(series_list, list) or not series_list:
        return ""
    first_series = series_list[0]
    if isinstance(first_series, dict):
        return _normalize_uid(first_series.get("uid"))
    return ""


def extract_dicomweb_root(context: Any) -> str:
    value = extract_identifier_value(context, HUB_DICOMWEB_ROOT)
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        return value.rstrip("/")
    return ""


def extract_nifti_download_url(context: Any) -> str:
    value = extract_identifier_value(context, HUB_IDENTIFIER_NIFTI_URL)
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        return value
    return ""


def extract_nifti_filename(context: Any) -> str:
    return extract_identifier_value(context, HUB_IDENTIFIER_NIFTI_FILENAME)


def extract_idc_series_uid(context: Any) -> str:
    return _normalize_uid(extract_identifier_value(context, HUB_IDENTIFIER_IDC))


def extract_idc_source_bucket(context: Any) -> str:
    value = extract_identifier_value(
        context, HUB_IDENTIFIER_IDC_SOURCE_BUCKET
    ).lower()
    return "gcs" if value == "gcs" else "aws"


def _file_entry_url_raw(file_entry: Dict[str, Any]) -> str:
    url = file_entry.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    uri = file_entry.get("uri")
    if isinstance(uri, str) and uri.strip():
        return uri.strip()
    return ""


def _file_entry_name_raw(file_entry: Dict[str, Any]) -> str:
    file_name = file_entry.get("fileName")
    if isinstance(file_name, str) and file_name.strip():
        return file_name.strip()
    filename = file_entry.get("filename")
    if isinstance(filename, str) and filename.strip():
        return filename.strip()
    return ""


def _normalize_file_entry(file_entry: Any) -> Optional[HubFileEntry]:
    if not isinstance(file_entry, dict):
        return None

    data = file_entry.get("data")
    has_bytes = isinstance(data, (bytes, bytearray, memoryview))
    url_raw = _file_entry_url_raw(file_entry)

    if url_raw:
        parsed = urlparse(url_raw)
        if parsed.scheme not in _ALLOWED_FILE_URL_SCHEMES:
            return None
    elif not has_bytes:
        return None

    file_name = _file_entry_name_raw(file_entry)
    mime_type = file_entry.get("mimeType")
    role = file_entry.get("role")
    label = file_entry.get("label")
    entry: HubFileEntry = {
        "fileName": file_name,
        "mimeType": str(mime_type).strip() if isinstance(mime_type, str) else "",
        "role": str(role).strip() if isinstance(role, str) else "",
        "label": str(label).strip() if isinstance(label, str) else "",
    }
    if url_raw:
        entry["url"] = url_raw
    if has_bytes:
        entry["data"] = bytes(data)
    return entry


def extract_imaging_study_files(context: Any) -> List[HubFileEntry]:
    files_resource = extract_study_context_item(context, "files")
    raw_files = (
        files_resource.get("files")
        if isinstance(files_resource, dict)
        else None
    )
    from_context: List[HubFileEntry] = []
    if isinstance(raw_files, list):
        for entry in raw_files:
            normalized = _normalize_file_entry(entry)
            if normalized:
                from_context.append(normalized)
    if from_context:
        return from_context

    legacy_url = extract_nifti_download_url(context)
    if not legacy_url:
        return []
    return [
        {
            "url": legacy_url,
            "fileName": extract_nifti_filename(context),
            "mimeType": "",
            "role": "",
            "label": "",
        }
    ]


def extract_open_mode(context: Any) -> str:
    explicit = extract_identifier_value(context, HUB_OPEN_MODE)
    if explicit in (
        HUB_OPEN_MODE_DICOMWEB,
        HUB_OPEN_MODE_DICOM_URL,
        HUB_OPEN_MODE_FILES,
        HUB_OPEN_MODE_IDC,
        HUB_OPEN_MODE_LOCAL_DICOM,
    ):
        return explicit

    if extract_imaging_study_files(context):
        return HUB_OPEN_MODE_FILES
    if extract_nifti_download_url(context):
        return HUB_OPEN_MODE_FILES

    study_uid = extract_dicom_study_uid(context)
    if study_uid and not extract_nifti_download_url(context):
        return HUB_OPEN_MODE_DICOMWEB
    return ""


def resolve_imaging_study_open_plan(context: Any) -> Optional[ImagingStudyOpenPlan]:
    normalized = _normalize_context_items(context)
    mode = extract_open_mode(normalized)
    study_id = extract_study_display_id(normalized)
    if not mode:
        study_uid = extract_dicom_study_uid(normalized)
        if study_uid:
            return {
                "mode": HUB_OPEN_MODE_DICOMWEB,
                "study_id": study_id,
                "files": extract_imaging_study_files(normalized),
                "study_uid": study_uid,
                "series_uid": extract_dicom_series_uid(normalized),
                "dicomweb_root": extract_dicomweb_root(normalized),
            }
        return None

    plan: ImagingStudyOpenPlan = {
        "mode": mode,
        "study_id": study_id,
        "files": extract_imaging_study_files(normalized),
        "study_uid": extract_dicom_study_uid(normalized),
        "series_uid": extract_dicom_series_uid(normalized),
        "dicomweb_root": extract_dicomweb_root(normalized),
    }

    if mode == HUB_OPEN_MODE_DICOMWEB:
        files = plan.get("files") or []
        study_uid = str(plan.get("study_uid") or "").strip()
        if files and study_uid:
            plan["idc_fallback"] = {
                "study_uid": study_uid,
                "series_uid": (
                    extract_idc_series_uid(normalized)
                    or extract_dicom_series_uid(normalized)
                ),
                "source_bucket": extract_idc_source_bucket(normalized),
                "files": files,
            }

    return plan


def _normalize_context_items(context: Any) -> List[Any]:
    if isinstance(context, list):
        return context
    if not isinstance(context, dict):
        return []

    items: List[Any] = []
    for key, value in context.items():
        if isinstance(value, dict) and not isinstance(value, list):
            items.append({"key": key, "resource": value})
    return items if items else [context]


def normalize_imaging_study_context(message: Dict[str, Any]) -> List[Any]:
    event = message.get("event") or {}
    context = event.get("context")
    return _normalize_context_items(context)
