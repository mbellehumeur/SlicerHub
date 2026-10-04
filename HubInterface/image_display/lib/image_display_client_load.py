"""Load Hub ImagingStudy-open context into the Slicer scene (main thread only)."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse

import slicer

from imaging_study_context import (
    HUB_OPEN_MODE_DICOM_URL,
    HUB_OPEN_MODE_DICOMWEB,
    HUB_OPEN_MODE_FILES,
    HUB_OPEN_MODE_IDC,
    HUB_OPEN_MODE_LOCAL_DICOM,
    HubFileEntry,
    ImagingStudyOpenPlan,
    resolve_imaging_study_open_plan,
)

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

_DICOM_EXTENSIONS = (".dcm", ".dicom", ".ima")
_IDC_DOWNLOAD_CONCURRENCY = 20


def _require_dicom_database() -> None:
    dicom_database = getattr(slicer, "dicomDatabase", None)
    if dicom_database is None or not dicom_database.isOpen:
        raise RuntimeError(
            "DICOM database is not open. Open the DICOM module in Slicer first."
        )


def _is_zip_name(name: str) -> bool:
    return name.lower().endswith(".zip")


def _default_download_name(url: str, index: int) -> str:
    path = urlparse(url).path
    base = os.path.basename(path)
    return base if base else f"hub-file-{index}"


def _safe_local_name(name: str, index: int) -> str:
    cleaned = os.path.basename(str(name or "").strip())
    if cleaned:
        return cleaned
    return f"hub-file-{index}"


def _infer_filetype(file_path: str) -> str:
    lower = file_path.lower()
    if lower.endswith(".nii") or lower.endswith(".nii.gz"):
        return "Nifti"
    if lower.endswith(".nrrd"):
        return "NRRD"
    if lower.endswith(_DICOM_EXTENSIONS):
        return "DICOM"
    return "VolumeFile"


def _collect_dicom_paths(directory: str) -> List[str]:
    paths: List[str] = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            lower = filename.lower()
            if lower.endswith(_DICOM_EXTENSIONS) or "." not in filename:
                paths.append(full_path)
    return paths


def _normalize_loaded_nodes(loaded_nodes: Any) -> List[Any]:
    if loaded_nodes is None:
        return []
    if isinstance(loaded_nodes, list):
        return loaded_nodes
    return [loaded_nodes]


def _node_ids_from_nodes(nodes: Sequence[Any]) -> List[str]:
    ids: List[str] = []
    for node in nodes:
        if node is None:
            continue
        node_id = node.GetID() if hasattr(node, "GetID") else ""
        if node_id:
            ids.append(node_id)
    return ids


def _hub_file_https_url(url: str) -> str:
    """Resolve Hub file URLs to HTTPS (IDC public buckets use s3:// or gs://)."""
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https"):
        return text
    if scheme == "s3":
        bucket = parsed.netloc.strip()
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise RuntimeError(f"Invalid S3 file URL: {text}")
        encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
        return f"https://{bucket}.s3.amazonaws.com/{encoded_key}"
    if scheme == "gs":
        bucket = parsed.netloc.strip()
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise RuntimeError(f"Invalid GCS file URL: {text}")
        encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
        return f"https://storage.googleapis.com/{bucket}/{encoded_key}"
    return text


def _download_hub_file_entry(
    temp_dir: str, index: int, entry: HubFileEntry
) -> List[str]:
    local_paths: List[str] = []
    url = _hub_file_https_url(str(entry.get("url") or "").strip())
    file_name = _safe_local_name(str(entry.get("fileName") or ""), index)
    data = entry.get("data")

    if isinstance(data, (bytes, bytearray, memoryview)):
        local_path = os.path.join(temp_dir, file_name)
        with open(local_path, "wb") as handle:
            handle.write(bytes(data))
        if _is_zip_name(local_path):
            extract_dir = os.path.join(temp_dir, f"zip-{index}")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(local_path, "r") as archive:
                archive.extractall(extract_dir)
            local_paths.extend(_collect_dicom_paths(extract_dir))
            for root, _, names in os.walk(extract_dir):
                for name in names:
                    if not name.lower().endswith(".zip"):
                        local_paths.append(os.path.join(root, name))
        else:
            local_paths.append(local_path)
        return local_paths

    if not url:
        return local_paths

    download_name = file_name or _default_download_name(url, index)
    local_path = os.path.join(temp_dir, download_name)
    urllib.request.urlretrieve(url, local_path)

    if _is_zip_name(local_path):
        extract_dir = os.path.join(temp_dir, f"zip-{index}")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(local_path, "r") as archive:
            archive.extractall(extract_dir)
        for root, _, names in os.walk(extract_dir):
            for name in names:
                full = os.path.join(root, name)
                if not name.lower().endswith(".zip"):
                    local_paths.append(full)
    else:
        local_paths.append(local_path)
    return local_paths


def _load_dicom_paths(file_paths: List[str]) -> List[str]:
    if not file_paths:
        raise RuntimeError("No DICOM files found to load")
    _require_dicom_database()
    from DICOMLib import DICOMUtils

    loadables_by_plugin, load_enabled = DICOMUtils.getLoadablesFromFileLists(
        [file_paths]
    )
    if not load_enabled:
        raise RuntimeError("No DICOM loadables found for downloaded files")
    DICOMUtils.selectHighestConfidenceLoadables(loadables_by_plugin)
    return DICOMUtils.loadLoadables(loadables_by_plugin)


def download_hub_file_entries(
    files: Sequence[HubFileEntry],
) -> Tuple[str, List[str]]:
    """Download Hub file entries to a temp directory; returns (temp_dir, local_paths)."""
    temp_dir = tempfile.mkdtemp(prefix="hub-id-")
    local_paths: List[str] = []

    try:
        for index, entry in enumerate(files):
            local_paths.extend(_download_hub_file_entry(temp_dir, index, entry))

        deduped: List[str] = []
        seen = set()
        for path in local_paths:
            normalized = os.path.normpath(path)
            if normalized in seen or not os.path.isfile(normalized):
                continue
            seen.add(normalized)
            deduped.append(normalized)

        if not deduped:
            raise RuntimeError("No files downloaded from Hub open context")

        return temp_dir, deduped
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def download_idc_hub_file_entries(
    files: Sequence[HubFileEntry],
) -> Tuple[str, List[str]]:
    """Parallel download for IDC direct-bucket opens (many DICOM instances)."""
    temp_dir = tempfile.mkdtemp(prefix="hub-idc-")
    local_paths: List[str] = []
    concurrency = min(_IDC_DOWNLOAD_CONCURRENCY, max(1, len(files)))

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    _download_hub_file_entry, temp_dir, index, entry
                ): index
                for index, entry in enumerate(files)
            }
            for future in as_completed(futures):
                local_paths.extend(future.result())

        deduped: List[str] = []
        seen = set()
        for path in local_paths:
            normalized = os.path.normpath(path)
            if normalized in seen or not os.path.isfile(normalized):
                continue
            seen.add(normalized)
            deduped.append(normalized)

        if not deduped:
            raise RuntimeError("No IDC files downloaded from Hub open context")

        return temp_dir, deduped
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _load_downloaded_dicom_study(
    temp_dir: str, local_paths: List[str]
) -> Dict[str, Any]:
    dicom_paths = _collect_dicom_paths(temp_dir)
    if not dicom_paths:
        dicom_paths = [
            path
            for path in local_paths
            if _infer_filetype(path) == "DICOM"
            or path.lower().endswith(_DICOM_EXTENSIONS)
        ]
    if not dicom_paths:
        dicom_paths = list(local_paths)

    from DICOMLib import DICOMUtils

    _require_dicom_database()
    parent_dir = os.path.commonpath(dicom_paths) if dicom_paths else temp_dir
    if not os.path.isdir(parent_dir):
        parent_dir = temp_dir
    DICOMUtils.importDicom(parent_dir, copyFiles=False)

    loaded_node_ids = _load_dicom_paths(dicom_paths)
    if not loaded_node_ids:
        raise RuntimeError("DICOM import did not load any nodes into the scene")
    return {
        "loaded_node_ids": loaded_node_ids,
        "temp_dir": temp_dir,
        "load_status": "loaded",
        "error": "",
    }


def load_idc_study(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    files = plan.get("files") or []
    if not files:
        raise RuntimeError("idc open requires at least one file URL")

    study_uid = str(plan.get("study_uid") or "").strip()
    series_uid = str(plan.get("series_uid") or "").strip()
    LOGGER.info(
        "Image Display IDC parallel download (%d file(s), concurrency %d) "
        "study=%s series=%s",
        len(files),
        min(_IDC_DOWNLOAD_CONCURRENCY, len(files)),
        study_uid or "(unknown)",
        series_uid or "(unknown)",
    )

    temp_dir, local_paths = download_idc_hub_file_entries(files)
    try:
        return _load_downloaded_dicom_study(temp_dir, local_paths)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def load_dicom_url_study(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    files = plan.get("files") or []
    if not files:
        raise RuntimeError("dicom-url open requires at least one file URL")

    temp_dir, local_paths = download_hub_file_entries(files)
    try:
        return _load_downloaded_dicom_study(temp_dir, local_paths)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def load_files_study(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    files = plan.get("files") or []
    if not files:
        raise RuntimeError("files open requires at least one file entry")

    temp_dir, local_paths = download_hub_file_entries(files)
    loaded_node_ids: List[str] = []
    try:
        for local_path in local_paths:
            file_type = _infer_filetype(local_path)
            if file_type == "DICOM":
                loaded_node_ids.extend(_load_dicom_paths([local_path]))
                continue
            loaded = slicer.util.loadNodeFromFile(local_path, file_type, {})
            loaded_node_ids.extend(_node_ids_from_nodes(_normalize_loaded_nodes(loaded)))

        if not loaded_node_ids:
            raise RuntimeError("No volume nodes loaded from files open context")

        return {
            "loaded_node_ids": loaded_node_ids,
            "temp_dir": temp_dir,
            "load_status": "loaded",
            "error": "",
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def load_dicomweb_study(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    study_uid = str(plan.get("study_uid") or "").strip()
    dicomweb_root = str(plan.get("dicomweb_root") or "").strip()
    series_uid = str(plan.get("series_uid") or "").strip() or None

    if not study_uid:
        raise RuntimeError("dicomweb open requires a DICOM study UID")
    if not dicomweb_root:
        raise RuntimeError("dicomweb open requires a DICOMweb root URL")

    _require_dicom_database()
    from DICOMLib import DICOMUtils

    loaded_uids = DICOMUtils.importFromDICOMWeb(
        dicomWebEndpoint=dicomweb_root,
        studyInstanceUID=study_uid,
        seriesInstanceUID=series_uid,
    )
    if not loaded_uids:
        raise RuntimeError("DICOMweb import did not return any study UIDs")

    file_paths: List[str] = []
    for study_id in loaded_uids:
        for series_id in slicer.dicomDatabase.seriesForStudy(study_id):
            for instance in slicer.dicomDatabase.instancesForSeries(series_id):
                file_paths.append(slicer.dicomDatabase.fileForInstance(instance))

    loaded_node_ids = _load_dicom_paths(file_paths)
    if not loaded_node_ids:
        raise RuntimeError("DICOMweb import did not load any nodes into the scene")

    return {
        "loaded_node_ids": loaded_node_ids,
        "temp_dir": "",
        "load_status": "loaded",
        "error": "",
    }


def _idc_plan_from_fallback(
    plan: ImagingStudyOpenPlan,
) -> ImagingStudyOpenPlan:
    fallback = plan.get("idc_fallback") or {}
    files = fallback.get("files") or []
    return {
        "mode": HUB_OPEN_MODE_IDC,
        "study_id": str(plan.get("study_id") or "").strip(),
        "study_uid": str(fallback.get("study_uid") or plan.get("study_uid") or "").strip(),
        "series_uid": str(
            fallback.get("series_uid") or plan.get("series_uid") or ""
        ).strip(),
        "files": files,
    }


def load_dicomweb_study_with_fallback(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    fallback = plan.get("idc_fallback") or {}
    fallback_files = fallback.get("files") or []
    dicomweb_root = str(plan.get("dicomweb_root") or "").strip()

    if fallback_files and not dicomweb_root:
        return load_idc_study(_idc_plan_from_fallback(plan))

    if not dicomweb_root:
        raise RuntimeError("dicomweb open requires urn:cast:dicomweb-root")

    try:
        return load_dicomweb_study(plan)
    except Exception as exc:
        if fallback_files:
            LOGGER.warning(
                "Image Display DICOMweb failed; using IDC bucket fallback: %s",
                exc,
            )
            return load_idc_study(_idc_plan_from_fallback(plan))
        raise


def clear_loaded_study(
    node_ids: Sequence[str], temp_dir: str
) -> None:
    reset_image_display_viewports()

    for node_id in node_ids:
        if not node_id:
            continue
        node = slicer.mrmlScene.GetNodeByID(node_id)
        if node is not None:
            slicer.mrmlScene.RemoveNode(node)

    if temp_dir and os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    force_image_display_render()


def reset_image_display_viewports() -> None:
    """Unlink volumes from slice views so the UI does not show stale pixels."""
    try:
        slicer.util.setSliceViewerLayers(
            background=None,
            foreground=None,
            label=None,
        )
    except Exception as exc:
        LOGGER.warning("Image Display viewport reset failed: %s", exc)


def force_image_display_render() -> None:
    try:
        slicer.util.forceRenderAllViews()
        slicer.app.processEvents()
    except Exception as exc:
        LOGGER.warning("Image Display force render failed: %s", exc)


def load_local_dicom_study(plan: ImagingStudyOpenPlan) -> Dict[str, Any]:
    """Load a study already present in ``slicer.dicomDatabase`` by StudyInstanceUID."""
    study_uid = str(plan.get("study_uid") or "").strip()
    if not study_uid:
        raise RuntimeError("local-dicom open requires a DICOM study UID")

    _require_dicom_database()

    file_paths: List[str] = []
    try:
        series_list = slicer.dicomDatabase.seriesForStudy(study_uid) or []
    except Exception as exc:
        raise RuntimeError(
            f"local-dicom: study not found in DICOM database ({study_uid})"
        ) from exc

    if not series_list:
        raise RuntimeError(
            f"local-dicom: no series for study {study_uid} in DICOM database"
        )

    for series_id in series_list:
        for instance in slicer.dicomDatabase.instancesForSeries(series_id) or []:
            path = slicer.dicomDatabase.fileForInstance(instance)
            if path:
                file_paths.append(path)

    loaded_node_ids = _load_dicom_paths(file_paths)
    if not loaded_node_ids:
        raise RuntimeError("local-dicom import did not load any nodes into the scene")

    return {
        "loaded_node_ids": loaded_node_ids,
        "temp_dir": "",
        "load_status": "loaded",
        "error": "",
    }


def load_imaging_study_open(context: Any) -> Dict[str, Any]:
    plan = resolve_imaging_study_open_plan(context)
    if plan is None:
        return {
            "loaded_node_ids": [],
            "temp_dir": "",
            "load_status": "error",
            "error": "Could not resolve imaging study open plan",
        }

    mode = str(plan.get("mode") or "").strip()
    study_id = str(plan.get("study_id") or "").strip()
    LOGGER.info(
        "Image Display loading study=%s mode=%s",
        study_id or "(unknown)",
        mode,
    )

    try:
        if mode == HUB_OPEN_MODE_DICOM_URL:
            result = load_dicom_url_study(plan)
        elif mode == HUB_OPEN_MODE_FILES:
            result = load_files_study(plan)
        elif mode == HUB_OPEN_MODE_DICOMWEB:
            result = load_dicomweb_study_with_fallback(plan)
        elif mode == HUB_OPEN_MODE_IDC:
            result = load_idc_study(plan)
        elif mode == HUB_OPEN_MODE_LOCAL_DICOM:
            result = load_local_dicom_study(plan)
        else:
            raise RuntimeError(f"Unsupported open mode: {mode or '(empty)'}")

        LOGGER.info(
            "Image Display loaded study=%s nodes=%s",
            study_id or "(unknown)",
            len(result.get("loaded_node_ids") or []),
        )
        return result
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        LOGGER.warning(
            "Image Display failed to load study=%s mode=%s: %s",
            study_id or "(unknown)",
            mode,
            message,
        )
        try:
            slicer.util.errorDisplay(
                f"Hub Image Display failed to load study: {message}"
            )
        except Exception:
            pass
        return {
            "loaded_node_ids": [],
            "temp_dir": "",
            "load_status": "error",
            "error": message,
        }
