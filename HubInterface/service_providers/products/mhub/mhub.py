#!/usr/bin/env python3
"""MHUB Cast resource server — standalone CLI entry point.

Run from repo root (plain Python, no 3D Slicer):

    pip install aiohttp
    python resource_servers/products/mhub/mhub.py
    python resource_servers/products/mhub/mhub.py --local

Default hub is SLICER-HUB-CLOUD; ``--local`` uses ``http://127.0.0.1:2018``.

On inbound dicom-send or nifti-send: status-update download line, download
files, write ``downloaded-files.txt``, simulate 10s processing with
status-update ``Processing`` every 3s to the requester, then ``Job complete``.

Vendored skill (unused by this stub): ``products/mhub/MHubSkill/`` from
https://github.com/MHubAI/MHubSkill — for later model discovery / workflows.

See ``resource_server.py`` and ``mhub-readme.md``.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_RS_ROOT = _SCRIPT_DIR.parent.parent
for _extra in (_SCRIPT_DIR, _RS_ROOT):
    _extra_str = str(_extra)
    if _extra.is_dir() and _extra_str not in sys.path:
        sys.path.insert(0, _extra_str)

from resource_server import (  # noqa: E402
    ResourceServerConfig,
    ResourceServerContext,
    ResourceServerHandlers,
    run_sync,
)

LOGGER = logging.getLogger("MHUB")

PRODUCT_NAME = "MHUB"
PROCESSING_SECONDS = 10
STATUS_UPDATE_INTERVAL_SECONDS = 3
FINAL_STATUS_LINE = "Job complete"


def build_status_response(_ctx: ResourceServerContext) -> Dict[str, Any]:
    return {
        "source": "status",
        "product": PRODUCT_NAME,
        "items": [{"key": "availability", "value": "online"}],
    }


def _context_files(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    event = message.get("event") or {}
    ctx = event.get("context")
    if isinstance(ctx, dict):
        files = ctx.get("files")
        if isinstance(files, list):
            return [entry for entry in files if isinstance(entry, dict)]
    return []


def _manifest_file_stats(message: Dict[str, Any]) -> Tuple[int, int]:
    files = _context_files(message)
    total_bytes = 0
    for entry in files:
        byte_length = entry.get("byteLength")
        if isinstance(byte_length, int) and byte_length >= 0:
            total_bytes += byte_length
    return len(files), total_bytes


def _format_download_status_line(file_count: int, total_bytes: int) -> str:
    if total_bytes > 0:
        total_mb = total_bytes / (1024 * 1024)
        mb_text = f"{total_mb:.1f}" if total_mb < 100 else f"{total_mb:.0f}"
        return (
            f"Downloading study, {file_count} files, total {mb_text} MB."
        )
    return f"Downloading study, {file_count} files."


def _publish_to_requester(
    ctx: ResourceServerContext, message: Dict[str, Any], status_line: str
) -> None:
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()
    target_subscriber = str(message.get("subscriber.name") or "").strip()
    if not topic or not target_subscriber:
        return
    http_status = ctx.publish_status_update_sync(
        topic, target_subscriber, status_line
    )
    LOGGER.info(
        "MHUB: status-update %r -> %s (HTTP %s)",
        status_line,
        target_subscriber,
        http_status,
    )


def on_send_download_start(
    ctx: ResourceServerContext, message: Dict[str, Any], _hub_event: str
) -> None:
    file_count, total_bytes = _manifest_file_stats(message)
    if file_count <= 0:
        LOGGER.warning(
            "MHUB: inbound send has no context.files[]; "
            "skipping download status-update"
        )
        return
    _publish_to_requester(
        ctx, message, _format_download_status_line(file_count, total_bytes)
    )


def _simulate_processing(
    ctx: ResourceServerContext, message: Dict[str, Any]
) -> None:
    deadline = time.monotonic() + PROCESSING_SECONDS
    next_update = time.monotonic()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_update:
            _publish_to_requester(ctx, message, "Processing")
            next_update += STATUS_UPDATE_INTERVAL_SECONDS
        time.sleep(0.1)


def _handle_inbound_send(
    ctx: ResourceServerContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
    label: str,
) -> None:
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()

    LOGGER.info(
        "MHUB: received %s id=%s topic=%s files=%d bytes=%d",
        label,
        message.get("id", ""),
        topic,
        file_count,
        total_bytes,
    )

    ctx.write_directory_manifest(input_dir)

    if not (event.get("hub.topic") or "").strip():
        LOGGER.warning("MHUB: inbound %s missing hub.topic", label)
        return
    if not str(message.get("subscriber.name") or "").strip():
        LOGGER.warning(
            "MHUB: inbound %s missing subscriber.name; "
            "cannot send status-update",
            label,
        )
        return

    _simulate_processing(ctx, message)
    _publish_to_requester(ctx, message, FINAL_STATUS_LINE)
    LOGGER.info("MHUB: simulated processing complete")


def on_dicom_send(
    ctx: ResourceServerContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    _handle_inbound_send(
        ctx, message, input_dir, file_count, total_bytes, "dicom-send"
    )


def on_nifti_send(
    ctx: ResourceServerContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    _handle_inbound_send(
        ctx, message, input_dir, file_count, total_bytes, "nifti-send"
    )


HANDLERS = ResourceServerHandlers(
    on_dicom_send=on_dicom_send,
    on_nifti_send=on_nifti_send,
    on_send_download_start=on_send_download_start,
    build_status_response=build_status_response,
)


if __name__ == "__main__":
    run_sync(ResourceServerConfig(product_name=PRODUCT_NAME), HANDLERS)
