"""Hub-hosted sample study catalog helpers."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional

_HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HUB_SAMPLES_ORG = "hub"
HUB_SAMPLES_ORG_LABEL = "Hub samples"


def _hub_samples_root() -> str:
    override = os.environ.get("HUB_SAMPLES_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(_HUB_DIR, "samples")


def _hub_sample_path_is_safe(study_id: str, file_name: str) -> bool:
    if not study_id or not file_name:
        return False
    if ".." in study_id or ".." in file_name:
        return False
    if "/" in study_id or "\\" in study_id:
        return False
    if "/" in file_name or "\\" in file_name:
        return False
    return True


def _resolve_hub_sample_file_path(study_id: str, file_name: str) -> Optional[str]:
    if not _hub_sample_path_is_safe(study_id, file_name):
        return None
    root = _hub_samples_root()
    study_dir = os.path.normpath(os.path.join(root, study_id))
    candidate = os.path.normpath(os.path.join(study_dir, file_name))
    study_prefix = study_dir + os.sep
    if not candidate.startswith(study_prefix):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def _guess_sample_media_type(file_name: str, declared: str = "") -> str:
    if declared:
        return declared
    lower = file_name.lower()
    ext_map = {
        ".dcm": "application/dicom",
        ".nrrd": "application/octet-stream",
        ".nii.gz": "application/octet-stream",
        ".nii": "application/octet-stream",
        ".zip": "application/zip",
    }
    for ext, media_type in ext_map.items():
        if lower.endswith(ext):
            return media_type
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def _load_hub_sample_study(study_dir: str, folder_name: str) -> Optional[Dict[str, Any]]:
    manifest_path = os.path.join(study_dir, "study.json")
    if not os.path.isfile(manifest_path):
        logging.getLogger("hub").warning(
            "Hub sample skip %s: missing study.json", folder_name
        )
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            meta = json.load(manifest_file)
    except (OSError, json.JSONDecodeError) as err:
        logging.getLogger("hub").warning(
            "Hub sample skip %s: invalid study.json: %s", folder_name, err
        )
        return None

    study_id = str(meta.get("id") or "").strip()
    if not study_id:
        logging.getLogger("hub").warning(
            "Hub sample skip %s: missing id in study.json", folder_name
        )
        return None
    if study_id != folder_name:
        logging.getLogger("hub").warning(
            "Hub sample skip %s: id %s does not match folder name",
            folder_name,
            study_id,
        )
        return None

    files_raw = meta.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        logging.getLogger("hub").warning(
            "Hub sample skip %s: files[] required", folder_name
        )
        return None

    files: List[Dict[str, str]] = []
    for entry in files_raw:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get("fileName") or "").strip()
        if not file_name:
            continue
        if not _resolve_hub_sample_file_path(study_id, file_name):
            logging.getLogger("hub").warning(
                "Hub sample skip %s: missing file %s", study_id, file_name
            )
            return None
        file_out: Dict[str, str] = {"fileName": file_name}
        for key in ("label", "mimeType", "role"):
            value = entry.get(key)
            if value:
                file_out[key] = str(value).strip()
        files.append(file_out)

    if not files:
        return None

    study: Dict[str, Any] = {
        "id": study_id,
        "name": str(meta.get("name") or study_id).strip(),
        "description": str(meta.get("description") or "").strip(),
        "files": files,
    }
    open_mode = str(meta.get("openMode") or meta.get("open_mode") or "").strip()
    if open_mode:
        study["openMode"] = open_mode
    format_value = str(meta.get("format") or "").strip()
    if format_value:
        study["format"] = format_value
    size = meta.get("size")
    if size:
        study["size"] = str(size).strip()
    if meta.get("openDisabled") is True:
        study["openDisabled"] = True
    open_disabled_reason = meta.get("openDisabledReason") or meta.get("open_disabled_reason")
    if open_disabled_reason:
        study["openDisabledReason"] = str(open_disabled_reason).strip()
    return study


def _scan_hub_sample_studies() -> List[Dict[str, Any]]:
    root = _hub_samples_root()
    if not os.path.isdir(root):
        return []
    studies: List[Dict[str, Any]] = []
    for entry in sorted(os.listdir(root)):
        if entry.startswith("."):
            continue
        study_dir = os.path.join(root, entry)
        if not os.path.isdir(study_dir):
            continue
        study = _load_hub_sample_study(study_dir, entry)
        if study:
            studies.append(study)
    return studies


def _hub_samples_catalog(base_url: str) -> Dict[str, Any]:
    base = base_url.rstrip("/")
    studies: List[Dict[str, Any]] = []
    for study in _scan_hub_sample_studies():
        study_out: Dict[str, Any] = {
            "id": study["id"],
            "name": study["name"],
            "description": study.get("description", ""),
            "organization": HUB_SAMPLES_ORG,
            "files": [],
        }
        if study.get("size"):
            study_out["size"] = study["size"]
        if study.get("openMode"):
            study_out["openMode"] = study["openMode"]
        if study.get("format"):
            study_out["format"] = study["format"]
        if study.get("openDisabled"):
            study_out["openDisabled"] = True
        if study.get("openDisabledReason"):
            study_out["openDisabledReason"] = study["openDisabledReason"]
        for file_entry in study["files"]:
            file_name = file_entry["fileName"]
            file_out: Dict[str, str] = {
                "url": f"{base}/api/hub/samples/files/{study['id']}/{file_name}",
                "fileName": file_name,
            }
            for key in ("label", "mimeType", "role"):
                if file_entry.get(key):
                    file_out[key] = file_entry[key]
            study_out["files"].append(file_out)
        studies.append(study_out)
    return {
        "organization": HUB_SAMPLES_ORG,
        "organizationLabel": HUB_SAMPLES_ORG_LABEL,
        "studies": studies,
    }
