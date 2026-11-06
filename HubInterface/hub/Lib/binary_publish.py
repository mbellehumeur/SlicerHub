"""Binary multipart publish helpers and payloadId fan-out rewrite."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from hub_client import is_hub_binary_event
from filename_policy import (
    FilenamePolicyError,
    enforce_transfer_filenames_for_notification,
)

from env import (
    HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES,
    HUB_HTTP_PAYLOAD_STORE_CHUNK_BYTES,
    HUB_HTTP_PAYLOAD_TTL_SECONDS,
)
from payload_store import _http_payload_store
from state import hub


def _http_exception_for_filename_policy(exc: FilenamePolicyError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "message": str(exc),
            "code": exc.code,
            "fileName": exc.file_name,
        },
    )


def _enforce_binary_transfer_filenames(
    notification: dict,
    *,
    require_name: bool = False,
) -> None:
    """Reject publishes whose transfer filenames fail hub allowlist policy."""
    event = notification.get("event") or {}
    if not is_hub_binary_event(event.get("hub.event")):
        return
    try:
        enforce_transfer_filenames_for_notification(
            notification, require_name=require_name
        )
    except FilenamePolicyError as exc:
        hub.log(
            f"filename policy rejected publish: code={exc.code} "
            f"file={exc.file_name or '(none)'} detail={exc}"
        )
        raise _http_exception_for_filename_policy(exc) from exc


def _notification_has_embedded_resource_bytes(notification: dict) -> bool:
    """True when a JSON publish carries inline ``context.files[].data`` bytes."""
    for entry in _context_files_from_notification(notification):
        data = entry.get("data")
        if data is None:
            continue
        if isinstance(data, str):
            if data.strip():
                return True
            continue
        if isinstance(data, (bytes, bytearray, memoryview)):
            if len(data) > 0:
                return True
            continue
        return True
    return False


def _reject_json_binary_bytes_publish(notification: dict) -> None:
    """Binary-family publishes with file bytes must use multipart/related binary batch."""
    event = notification.get("event") or {}
    if not is_hub_binary_event(event.get("hub.event")):
        return
    if _notification_has_embedded_resource_bytes(notification):
        raise HTTPException(
            status_code=400,
            detail=(
                "binary-family publish with file bytes requires "
                "multipart/related binary batch (JSON + context.files[] parts)"
            ),
        )


def _context_files_from_notification(notification: dict) -> List[dict]:
    """Return ``context.files[]`` entries from a binary-batch-style batch publish."""
    event = notification.get("event") or {}
    ctx = event.get("context")
    if not isinstance(ctx, dict):
        return []
    files = ctx.get("files")
    if not isinstance(files, list):
        return []
    return [entry for entry in files if isinstance(entry, dict)]


def _split_store_chunks(raw: bytes, chunk_size: int) -> List[bytes]:
    """Split ``raw`` into storage chunks (one chunk when ``chunk_size <= 0``)."""
    if chunk_size <= 0 or len(raw) <= chunk_size:
        return [raw]
    return [raw[offset : offset + chunk_size] for offset in range(0, len(raw), chunk_size)]


def _file_has_publish_url(entry: dict) -> bool:
    """True when ``files[]`` entry carries an HTTP(S) download URL (no hub upload)."""
    url = entry.get("url")
    if not isinstance(url, str):
        return False
    u = url.strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def _file_indices_needing_binary_parts(files_meta: List[dict]) -> List[int]:
    return [idx for idx, entry in enumerate(files_meta) if not _file_has_publish_url(entry)]


def _rewrite_files_with_payload_ids(
    notification: dict,
    file_registrations: List[Optional[Dict[str, Any]]],
) -> str:
    """Build WS text JSON with hub-added ``payloadIds[]`` on uploaded ``context.files[]`` entries."""
    n2 = copy.deepcopy(notification)
    ev2 = n2.get("event") or {}
    ctx = ev2.get("context")
    if not isinstance(ctx, dict):
        return json.dumps(n2)
    files = ctx.get("files")
    if not isinstance(files, list):
        return json.dumps(n2)
    for idx, entry in enumerate(files):
        if not isinstance(entry, dict):
            continue
        stripped = copy.deepcopy(entry)
        stripped.pop("data", None)
        stripped.pop("binaryTransfer", None)
        stripped.pop("payloadId", None)
        registered = file_registrations[idx] if idx < len(file_registrations) else None
        if registered is not None:
            stripped.pop("url", None)
            stripped["byteLength"] = sum(registered["chunkByteLengths"])
            stripped["payloadIds"] = list(registered["payloadIds"])
            stripped["chunkByteLengths"] = list(registered["chunkByteLengths"])
            stripped["expiresAt"] = registered["expiresAt"]
        else:
            stripped.pop("payloadIds", None)
            stripped.pop("chunkByteLengths", None)
            stripped.pop("expiresAt", None)
        files[idx] = stripped
    return json.dumps(n2)


def _prepare_binary_batch_fanout(
    notification: dict,
    blobs_by_file_index: Dict[int, bytes],
) -> str:
    """Store uploaded binary batch files and fan out ``payloadIds[]`` for those entries only."""
    files = _context_files_from_notification(notification)
    if not blobs_by_file_index:
        return _fanout_metadata_only_binary(notification)

    _enforce_binary_transfer_filenames(notification, require_name=True)

    chunk_size = HUB_HTTP_PAYLOAD_STORE_CHUNK_BYTES
    file_registrations: List[Optional[Dict[str, Any]]] = [None] * len(files)
    total_chunks = 0
    stored_chunks = 0
    for idx, raw in blobs_by_file_index.items():
        if idx < 0 or idx >= len(files):
            continue
        entry = files[idx]
        file_name = str(entry.get("fileName") or "").strip()
        mime_type = str(entry.get("mimeType") or "application/dicom").strip()
        chunks = _split_store_chunks(raw, chunk_size)
        total_chunks += len(chunks)
        payload_ids: List[str] = []
        chunk_byte_lengths: List[int] = []
        expires_at: Optional[datetime] = None
        file_ok = True
        for chunk_idx, chunk in enumerate(chunks):
            registered = _http_payload_store.register(
                chunk,
                file_name=file_name,
                mime_type=mime_type or "application/dicom",
                ttl_seconds=HUB_HTTP_PAYLOAD_TTL_SECONDS,
                max_total_bytes=HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES,
            )
            if registered is None:
                file_ok = False
                break
            payload_id, chunk_expires_at = registered
            payload_ids.append(payload_id)
            chunk_byte_lengths.append(len(chunk))
            expires_at = chunk_expires_at
            stored_chunks += 1
            hub.log(
                f"Stored binary batch chunk payloadId={payload_id[:8]}... "
                f"bytes={len(chunk)} index={idx} "
                f"chunk={chunk_idx + 1}/{len(chunks)} "
                f"file={file_name or '(unnamed)'}"
            )
        if file_ok and expires_at is not None:
            file_registrations[idx] = {
                "payloadIds": payload_ids,
                "chunkByteLengths": chunk_byte_lengths,
                "expiresAt": expires_at.isoformat(),
            }

    if stored_chunks == total_chunks and all(
        file_registrations[idx] is not None for idx in blobs_by_file_index
    ):
        return _rewrite_files_with_payload_ids(notification, file_registrations)

    hub.log(
        "binary batch payload store partially unavailable "
        f"(stored={stored_chunks}/{total_chunks} "
        f"inflight={_http_payload_store.total_bytes()}B "
        f"cap={HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES}B), "
        "fanning out metadata-only for this publish"
    )
    return _fanout_metadata_only_binary(notification)


def _prepare_websocket_fanout_text(
    notification: dict,
    notification_json: str,
    predecoded_binary_by_index: Optional[Dict[int, bytes]] = None,
) -> str:
    """
    WebSocket fan-out is text-only. binary batch publish publishes store each file and
    rewrite ``context.files[].payloadIds[]``; metadata-only binary-family events
    fan out without payload ids.
    """
    event = notification.get("event") or {}
    if not is_hub_binary_event(event.get("hub.event")):
        return notification_json

    if predecoded_binary_by_index is not None:
        return _prepare_binary_batch_fanout(notification, predecoded_binary_by_index)

    return _fanout_metadata_only_binary(notification)


def _fanout_metadata_only_binary(notification: dict) -> str:
    """Return JSON envelope with inline bytes stripped; preserve ``url`` for direct download."""
    n2 = copy.deepcopy(notification)
    ev2 = n2.get("event") or {}
    ctx = ev2.get("context")
    if isinstance(ctx, dict):
        files = ctx.get("files")
        if isinstance(files, list):
            cleaned_files: List[dict] = []
            for entry in files:
                if not isinstance(entry, dict):
                    cleaned_files.append(entry)
                    continue
                cleaned = dict(entry)
                cleaned.pop("data", None)
                cleaned.pop("binaryTransfer", None)
                cleaned.pop("payloadId", None)
                cleaned.pop("payloadIds", None)
                cleaned.pop("chunkByteLengths", None)
                cleaned.pop("expiresAt", None)
                cleaned_files.append(cleaned)
            ctx["files"] = cleaned_files
        res = ctx.get("resource")
        if isinstance(res, dict):
            cleaned = dict(res)
            cleaned.pop("data", None)
            cleaned.pop("binaryTransfer", None)
            cleaned.pop("payloadId", None)
            cleaned.pop("payloadIds", None)
            cleaned.pop("chunkByteLengths", None)
            cleaned.pop("expiresAt", None)
            ctx["resource"] = cleaned
    elif isinstance(ctx, list):
        for item in ctx:
            if not isinstance(item, dict):
                continue
            res = item.get("resource")
            if not isinstance(res, dict):
                continue
            cleaned = dict(res)
            cleaned.pop("data", None)
            cleaned.pop("binaryTransfer", None)
            cleaned.pop("payloadId", None)
            cleaned.pop("payloadIds", None)
            cleaned.pop("chunkByteLengths", None)
            cleaned.pop("expiresAt", None)
            item["resource"] = cleaned
    return json.dumps(n2)


def _iter_http_payload_chunks(
    token: str,
    raw: bytes,
    file_name: str,
    chunk_size: int,
):
    """Yield payload bytes in large chunks; log real wire time after last chunk."""
    started_at = time.monotonic()
    nbytes = len(raw)
    offset = 0
    while offset < nbytes:
        end = min(offset + chunk_size, nbytes)
        yield raw[offset:end]
        offset = end
    elapsed = max(time.monotonic() - started_at, 0.0)
    throughput = (
        f"{(nbytes / (1024 * 1024)) / elapsed:.2f}"
        if elapsed > 0
        else "n/a"
    )
    hub.log(
        f"Served http payload token={token[:8]}... "
        f"bytes={nbytes} elapsed={elapsed:.2f}s "
        f"throughput={throughput} MB/s file={file_name or '(unnamed)'}"
    )

async def _read_multipart_part_bytes(part: Any) -> bytes:
    if part is None:
        return b""
    read = getattr(part, "read", None)
    if callable(read):
        data = read()
        if asyncio.iscoroutine(data):
            data = await data
        return bytes(data or b"")
    if isinstance(part, (bytes, bytearray)):
        return bytes(part)
    return str(part).encode("utf-8")


def _parse_multipart_related_parts(
    body: bytes, content_type: str
) -> List[Tuple[str, bytes]]:
    """Parse ``multipart/related`` body into ``(content_type, payload)`` pairs."""
    if not body:
        return []
    header = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n"
    ).encode("ascii", errors="replace")
    msg = BytesParser(policy=policy.default).parsebytes(header + body)
    parts: List[Tuple[str, bytes]] = []
    for part in msg.iter_parts():
        ct = part.get_content_type() or "application/octet-stream"
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        else:
            payload = bytes(payload)
        parts.append((ct, payload))
    return parts


async def _parse_binary_batch_publish(request: Request) -> tuple:
    """Parse binary-batch-shaped ``multipart/related`` batch (JSON + N file parts)."""
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="multipart/related body is empty")

    parts = _parse_multipart_related_parts(body, content_type)
    if not parts:
        raise HTTPException(status_code=400, detail="multipart/related has no parts")

    json_ct, json_bytes = parts[0]
    json_ct_lower = json_ct.lower()
    if "json" not in json_ct_lower:
        raise HTTPException(
            status_code=400,
            detail=(
                "binary batch publish first part must be JSON "
                f"(application/dicom+json); got {json_ct!r}"
            ),
        )
    if not json_bytes:
        raise HTTPException(status_code=400, detail="binary batch publish JSON part is empty")
    try:
        notification = json.loads(json_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"binary batch publish JSON part is not valid JSON: {exc}"
        ) from exc

    if not isinstance(notification, dict):
        raise HTTPException(status_code=400, detail="binary batch publish JSON must be an object")

    event = notification.get("event")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="binary batch publish JSON missing event object")
    if not is_hub_binary_event(event.get("hub.event")):
        raise HTTPException(
            status_code=400,
            detail=(
                "binary batch publish publish is only supported for binary-family events "
                "(hub.event starting with dicom, nifti, jpg, png, nrrd, imagingstudy, or scene)"
            ),
        )

    files_meta = _context_files_from_notification(notification)
    if not files_meta:
        raise HTTPException(
            status_code=400,
            detail="binary batch publish publish requires non-empty event.context.files[]",
        )

    file_parts = parts[1:]
    indices_needing_parts = _file_indices_needing_binary_parts(files_meta)
    if len(file_parts) != len(indices_needing_parts):
        raise HTTPException(
            status_code=400,
            detail=(
                f"binary batch publish expects {len(indices_needing_parts)} file part(s) "
                f"after JSON (files without HTTP URL), got {len(file_parts)}"
            ),
        )

    allowed_part_types = (
        "application/dicom",
        "application/octet-stream",
        "application/vnd.unknown.nifti-1",
    )
    blobs_by_file_index: Dict[int, bytes] = {}
    for part_idx, (part_ct, raw) in enumerate(file_parts):
        if not raw:
            raise HTTPException(
                status_code=400, detail=f"binary batch publish file part {part_idx} is empty"
            )
        file_idx = indices_needing_parts[part_idx]
        part_ct_lower = part_ct.lower().split(";")[0].strip()
        entry = files_meta[file_idx]
        expected_mime = str(entry.get("mimeType") or "application/octet-stream").strip().lower()
        if expected_mime:
            expected_mime = expected_mime.split(";")[0].strip()
        if part_ct_lower not in allowed_part_types and (
            not expected_mime or part_ct_lower != expected_mime
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"binary batch publish file part {part_idx} has unsupported Content-Type "
                    f"{part_ct!r} (expected {expected_mime or 'application/dicom'})"
                ),
            )
        expected = entry.get("byteLength")
        if isinstance(expected, int) and expected >= 0 and expected != len(raw):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"binary batch publish file {file_idx} size {len(raw)} does not match "
                    f"context.files[{file_idx}].byteLength {expected}"
                ),
            )
        blobs_by_file_index[file_idx] = raw

    _enforce_binary_transfer_filenames(notification, require_name=True)
    return notification, blobs_by_file_index
