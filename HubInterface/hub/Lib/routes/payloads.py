"""GET /api/hub/payloads/{token}."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from binary_publish import _iter_http_payload_chunks
from env import HUB_HTTP_PAYLOAD_SEND_CHUNK_BYTES
from http_util import _enforce_bearer_auth
from payload_store import _http_payload_store

router = APIRouter()


@router.get("/api/hub/payloads/{token}")
async def get_hub_payload(request: Request, token: str):
    """Serve a stored http binaryTransfer payload by token."""
    _enforce_bearer_auth(request)
    entry = _http_payload_store.get(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="payload not found or expired")
    headers = {
        "Content-Length": str(entry.byte_length),
        "Cache-Control": "no-store",
    }
    if entry.file_name:
        # RFC 5987 fallback would be nicer for non-ASCII but the existing
        # nifti-send filenames are ASCII in practice.
        headers["Content-Disposition"] = (
            f'attachment; filename="{entry.file_name}"'
        )
    return StreamingResponse(
        _iter_http_payload_chunks(
            token,
            entry.raw,
            entry.file_name or "",
            HUB_HTTP_PAYLOAD_SEND_CHUNK_BYTES,
        ),
        media_type=entry.mime_type or "application/octet-stream",
        headers=headers,
    )
