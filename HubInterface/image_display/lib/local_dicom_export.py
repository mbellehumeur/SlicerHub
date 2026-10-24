"""Export local Slicer DICOM database studies for hub dicom-send."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

LOGGER = logging.getLogger("HubInterface.ImageDisplay")


def _instance_tag(db: Any, instance: str, tag: str) -> str:
    try:
        value = db.instanceValue(instance, tag)
    except Exception:
        return ""
    return str(value or "").strip()


def collect_local_dicom_file_paths(
    study_instance_uid: str,
    series_instance_uid: str = "",
) -> Tuple[str, List[str]]:
    """Return ``(studyUID, filePaths)`` from ``slicer.dicomDatabase``.

    Raises ``RuntimeError`` when the study/series cannot be resolved or has no files.
    """
    study_uid = str(study_instance_uid or "").strip()
    if not study_uid:
        raise RuntimeError("dicom-request requires studyInstanceUID")

    try:
        import slicer
    except Exception as exc:
        raise RuntimeError("slicer module unavailable") from exc

    db = getattr(slicer, "dicomDatabase", None)
    if db is None:
        raise RuntimeError("slicer.dicomDatabase is unavailable")

    try:
        series_list = db.seriesForStudy(study_uid) or []
    except Exception as exc:
        raise RuntimeError(
            f"dicom-request: study not found in DICOM database ({study_uid})"
        ) from exc

    if not series_list:
        raise RuntimeError(
            f"dicom-request: no series for study {study_uid} in DICOM database"
        )

    series_filter = str(series_instance_uid or "").strip()
    file_paths: List[str] = []
    for series_id in series_list:
        series_key = str(series_id or "").strip()
        if not series_key:
            continue
        try:
            instances = db.instancesForSeries(series_key) or []
        except Exception:
            continue
        if series_filter:
            # seriesForStudy may return SeriesInstanceUID or an internal key.
            series_uid = series_key
            if instances:
                tagged = _instance_tag(db, instances[0], "0020,000E")
                if tagged:
                    series_uid = tagged
            if series_uid != series_filter and series_key != series_filter:
                continue
        for instance in instances:
            path = db.fileForInstance(instance)
            if path and os.path.isfile(path):
                file_paths.append(str(path))

    if not file_paths:
        if series_filter:
            raise RuntimeError(
                f"dicom-request: no files for series {series_filter} in study {study_uid}"
            )
        raise RuntimeError(
            f"dicom-request: no files for study {study_uid} in DICOM database"
        )

    return study_uid, file_paths


def build_dicom_send_message_from_paths(
    *,
    topic: str,
    file_paths: List[str],
) -> Dict[str, Any]:
    """Build a multipart-ready ``dicom-send`` message with inline file bytes."""
    files: List[Dict[str, Any]] = []
    for index, path in enumerate(file_paths):
        with open(path, "rb") as dcm_file:
            raw = dcm_file.read()
        base = os.path.basename(path) or f"dicom-send-{index + 1}.dcm"
        if not base.lower().endswith(".dcm"):
            base = f"{base}.dcm"
        files.append(
            {
                "fileName": base,
                "mimeType": "application/dicom",
                "byteLength": len(raw),
                "data": raw,
            }
        )
    if not files:
        raise RuntimeError("dicom-request: no readable DICOM files to publish")

    return {
        "event": {
            "hub.topic": topic,
            "hub.event": "dicom-send",
            "context": {"files": files},
        }
    }


def export_local_dicom_send_message(
    *,
    topic: str,
    study_instance_uid: str,
    series_instance_uid: str = "",
) -> Tuple[Dict[str, Any], str, int]:
    """Collect local DICOM files and build a ``dicom-send`` publish message.

    Returns ``(message, studyUID, fileCount)``.
    """
    study_uid, paths = collect_local_dicom_file_paths(
        study_instance_uid, series_instance_uid
    )
    message = build_dicom_send_message_from_paths(topic=topic, file_paths=paths)
    return message, study_uid, len(paths)
