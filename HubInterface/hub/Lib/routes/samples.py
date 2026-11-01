"""GET /api/hub/samples catalog and file download."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from public_url import _request_public_base_url
from samples import (
    _guess_sample_media_type,
    _hub_samples_catalog,
    _hub_samples_root,
    _resolve_hub_sample_file_path,
)

router = APIRouter()


@router.get("/api/hub/samples")
@router.get("/api/hub/samples/")
async def get_hub_samples(request: Request):
    """Worklist catalog for hub-hosted sample studies under hub/samples/."""
    return _hub_samples_catalog(_request_public_base_url(request))


@router.get("/api/hub/samples/files/{study_id}/{file_name}")
async def get_hub_sample_file(study_id: str, file_name: str):
    """Download one file from a hub sample study folder."""
    file_path = _resolve_hub_sample_file_path(study_id, file_name)
    if not file_path:
        raise HTTPException(status_code=404, detail="Sample file not found")
    media_type = _guess_sample_media_type(file_name)
    study_dir = os.path.join(_hub_samples_root(), study_id)
    manifest_path = os.path.join(study_dir, "study.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                meta = json.load(manifest_file)
            for entry in meta.get("files") or []:
                if isinstance(entry, dict) and entry.get("fileName") == file_name:
                    media_type = _guess_sample_media_type(
                        file_name, str(entry.get("mimeType") or "").strip()
                    )
                    break
        except (OSError, json.JSONDecodeError):
            pass
    return FileResponse(file_path, media_type=media_type)
