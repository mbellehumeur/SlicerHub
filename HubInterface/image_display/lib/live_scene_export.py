"""Export current MRML LiveScene and publish ImagingStudy-open with hub blob payloads."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

LIVE_SCENE_SAMPLE_ID = "slicer-live-scene"
LIVESCENE_DATA_TYPE = "LIVESCENE"
LIVESCENE_MIME = "application/vnd.slicerlive.scene+json"
HUB_OPEN_MODE = "urn:cast:open-mode"
HUB_OPEN_MODE_FILES = "files"
HUB_IDENTIFIER_WORKLIST_SAMPLE_ID = "urn:cast:worklist-sample-id"


def _try_import_serialize_mrson():
    """Return serialize_mrson callable or None if LiveStory is unavailable."""
    candidates = []
    try:
        import slicer

        app = getattr(slicer, "app", None)
        if app is not None:
            home = getattr(app, "slicerHome", None) or ""
            if home:
                candidates.append(
                    os.path.join(home, "..", "SlicerLive", "LiveStory")
                )
    except Exception:
        pass

    # Common sibling checkout: .../pw46/SlicerLive/LiveStory next to hub-interface
    here = os.path.abspath(os.path.dirname(__file__))
    candidates.extend(
        [
            os.path.normpath(
                os.path.join(here, "..", "..", "..", "..", "..", "pw46", "SlicerLive", "LiveStory")
            ),
            os.path.normpath(
                os.path.join(here, "..", "..", "..", "..", "pw46", "SlicerLive", "LiveStory")
            ),
            os.path.expanduser("~/src/pw46/SlicerLive/LiveStory"),
            os.path.expanduser("~/slicer/SlicerLive/LiveStory"),
        ]
    )

    for path in candidates:
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            continue
        if path not in __import__("sys").path:
            __import__("sys").path.insert(0, path)
        try:
            from LiveStoryLib.serialize_mrson import serialize_mrson

            return serialize_mrson
        except Exception as exc:
            LOGGER.debug("serialize_mrson import failed from %s: %s", path, exc)
            continue

    try:
        from LiveStoryLib.serialize_mrson import serialize_mrson

        return serialize_mrson
    except Exception:
        return None


def export_live_scene_document() -> Tuple[Dict[str, Any], List[Tuple[str, bytes]]]:
    """Export current MRML to LiveScene JSON + content-addressed blob bytes.

    Returns ``(scene_doc, [(fileName, bytes), ...])``.
    """
    serialize_mrson = _try_import_serialize_mrson()
    if serialize_mrson is None:
        raise RuntimeError(
            "LiveStory serialize_mrson is not available. "
            "Install/open LiveStory so Hub can export the live scene."
        )

    outdir = tempfile.mkdtemp(prefix="hub-livescene-")
    try:
        result = serialize_mrson(outdir, "hub-live")
        scene_path = str((result or {}).get("scene") or "").strip()
        if not scene_path or not os.path.isfile(scene_path):
            raise RuntimeError("serialize_mrson did not write a scene JSON file")

        with open(scene_path, "r", encoding="utf-8") as fh:
            scene_doc = json.load(fh)
        if not isinstance(scene_doc, dict):
            raise RuntimeError("scene JSON must be an object")

        blobdir = os.path.join(outdir, "blobs")
        blob_parts: List[Tuple[str, bytes]] = []
        if os.path.isdir(blobdir):
            for name in sorted(os.listdir(blobdir)):
                path = os.path.join(blobdir, name)
                if not os.path.isfile(path):
                    continue
                with open(path, "rb") as bf:
                    blob_parts.append((name, bf.read()))

        # Logical prefix only — bytes travel as hub payloads, not HTTP under blobBase.
        scene_doc["blobBase"] = str(scene_doc.get("blobBase") or "blobs/").strip() or "blobs/"
        return scene_doc, blob_parts
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def build_livescene_hub_blobs_scene_update_message(
    *,
    topic: str,
    blob_parts: List[Tuple[str, bytes]],
) -> Dict[str, Any]:
    """Build multipart scene-update with context.files[] for hub blob store.

    Hub-mirror learns payloadIds from the scene-update fan-out (no ImagingStudy-open).
    """
    files: List[Dict[str, Any]] = []
    for file_name, raw in blob_parts:
        transfer_name = str(file_name or "").strip()
        if transfer_name.startswith("sha256-") and "." not in transfer_name:
            transfer_name = f"{transfer_name}.bin"
        files.append(
            {
                "fileName": transfer_name,
                "mimeType": "application/octet-stream",
                "role": "blob",
                "label": "livescene-blob",
                "byteLength": len(raw),
                "data": raw,
            }
        )

    return {
        "event": {
            "hub.topic": topic,
            "hub.event": "scene-update",
            "context": {
                "files": files,
                "livesync": {"event": "HubBlobs"},
            },
        }
    }


def build_livescene_imaging_study_open_message(
    *,
    topic: str,
    scene_doc: Dict[str, Any],
    blob_parts: List[Tuple[str, bytes]],
) -> Dict[str, Any]:
    """Build ImagingStudy-open with dict context (scene + files[]) for multipart publish."""
    files: List[Dict[str, Any]] = []
    for file_name, raw in blob_parts:
        # Hub filename policy requires an allowlisted suffix; content hashes alone are rejected.
        transfer_name = str(file_name or "").strip()
        if transfer_name.startswith("sha256-") and "." not in transfer_name:
            transfer_name = f"{transfer_name}.bin"
        files.append(
            {
                "fileName": transfer_name,
                "mimeType": "application/octet-stream",
                "role": "blob",
                "label": "livescene-blob",
                "byteLength": len(raw),
                "data": raw,
            }
        )

    study = {
        "resourceType": "ImagingStudy",
        "id": LIVE_SCENE_SAMPLE_ID,
        "status": "available",
        "identifier": [
            {"system": HUB_OPEN_MODE, "value": HUB_OPEN_MODE_FILES},
            {
                "system": HUB_IDENTIFIER_WORKLIST_SAMPLE_ID,
                "value": LIVE_SCENE_SAMPLE_ID,
            },
        ],
    }

    return {
        "event": {
            "hub.topic": topic,
            "hub.event": "ImagingStudy-open",
            "context": {
                "study": study,
                "scene": scene_doc,
                "files": files,
            },
        }
    }


async def publish_live_scene_open(client: Any, topic: str) -> None:
    """Export MRML on caller thread expectations: scene export must run on main thread first."""
    # Export is synchronous and must be invoked from main thread by the caller.
    scene_doc, blob_parts = export_live_scene_document()
    if not blob_parts:
        LOGGER.warning("LiveScene export produced no blob files (empty scene?)")

    msg = build_livescene_imaging_study_open_message(
        topic=topic,
        scene_doc=scene_doc,
        blob_parts=blob_parts,
    )
    status = await client.publish(msg)
    LOGGER.info(
        "Published ImagingStudy-open LiveScene nodes=%d blobs=%d http=%s",
        len(scene_doc.get("nodes") or {}),
        len(blob_parts),
        status,
    )


def live_scene_status_study_row() -> Dict[str, Any]:
    """Synthetic worklist row advertised in STATUS (no scene body)."""
    return {
        "id": LIVE_SCENE_SAMPLE_ID,
        "name": "Current 3D Slicer scene",
        "description": "Live MRML scene from connected 3D Slicer (Open publishes LiveScene)",
        "organization": "3d-slicer",
        "format": "LiveScene",
        "openMode": "live-scene",
        "modalities": ["LiveScene"],
    }
