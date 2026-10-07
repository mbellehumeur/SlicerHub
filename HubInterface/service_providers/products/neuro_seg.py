#!/usr/bin/env python3
"""NEURO_SEG Hub service provider — standalone CLI entry point.

Run from repo root (plain Python, no 3D Slicer):

    pip install aiohttp
    python service_providers/products/neuro_seg.py
    python service_providers/products/neuro_seg.py --local

Default hub is SLICER-HUB-CLOUD; ``--local`` uses ``http://127.0.0.1:2018``.

On inbound nifti-send: status-update ``Downloading NIfTI volume, …``, download
files, write ``downloaded-files.txt``, simulate 10s processing with status-update
``Processing`` every 3s to the requester, then ``Segmentation complete``.

See ``service_provider.py`` for the reusable framework and
``neuro_seg-readme.md`` for extension points (inference, status-update,
NIfTI result publish).
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_SP_ROOT = _SCRIPT_DIR.parent
for _extra in (_SCRIPT_DIR, _SP_ROOT):
    _extra_str = str(_extra)
    if _extra.is_dir() and _extra_str not in sys.path:
        sys.path.insert(0, _extra_str)

from service_provider import (  # noqa: E402
    ServiceProviderConfig,
    ServiceProviderContext,
    ServiceProviderHandlers,
    run_sync,
)

LOGGER = logging.getLogger("NEURO_SEG")

PRODUCT_NAME = "NEURO_SEG"
PROCESSING_SECONDS = 10
STATUS_UPDATE_INTERVAL_SECONDS = 3
FINAL_STATUS_LINE = "Segmentation complete"


def build_status_response(_ctx: ServiceProviderContext) -> Dict[str, Any]:
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
            f"Downloading NIfTI volume, {file_count} files, total {mb_text} MB."
        )
    return f"Downloading NIfTI volume, {file_count} files."


def _publish_to_requester(
    ctx: ServiceProviderContext, message: Dict[str, Any], status_line: str
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
        "NEURO_SEG: status-update %r -> %s (HTTP %s)",
        status_line,
        target_subscriber,
        http_status,
    )


def on_send_download_start(
    ctx: ServiceProviderContext, message: Dict[str, Any], _hub_event: str
) -> None:
    file_count, total_bytes = _manifest_file_stats(message)
    if file_count <= 0:
        LOGGER.warning(
            "NEURO_SEG: inbound send has no context.files[]; "
            "skipping download status-update"
        )
        return
    _publish_to_requester(
        ctx, message, _format_download_status_line(file_count, total_bytes)
    )


def _simulate_processing(
    ctx: ServiceProviderContext, message: Dict[str, Any]
) -> None:
    deadline = time.monotonic() + PROCESSING_SECONDS
    next_update = time.monotonic()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_update:
            _publish_to_requester(ctx, message, "Processing")
            next_update += STATUS_UPDATE_INTERVAL_SECONDS
        time.sleep(0.1)


def _resolve_input_nifti(input_dir: Path) -> Path | None:
    nifti_files = sorted(input_dir.glob("*.nii*"))
    if not nifti_files:
        return None
    return nifti_files[0]


def _handle_inbound_nifti_send(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()

    LOGGER.info(
        "NEURO_SEG: received nifti-send id=%s topic=%s files=%d bytes=%d",
        message.get("id", ""),
        topic,
        file_count,
        total_bytes,
    )

    ctx.write_directory_manifest(input_dir)

    if not topic:
        LOGGER.warning("NEURO_SEG: inbound nifti-send missing hub.topic")
        return
    if not str(message.get("subscriber.name") or "").strip():
        LOGGER.warning(
            "NEURO_SEG: inbound nifti-send missing subscriber.name; "
            "cannot send status-update"
        )
        return

    _publish_to_requester(ctx, message, "Download complete.")

    nifti_path = _resolve_input_nifti(input_dir)
    if nifti_path is None:
        _publish_to_requester(ctx, message, "ERROR: no NIfTI file in input")
        return

    LOGGER.info("NEURO_SEG: input NIfTI %s", nifti_path)

    _simulate_processing(ctx, message)
    _publish_to_requester(ctx, message, FINAL_STATUS_LINE)
    LOGGER.info("NEURO_SEG: simulated processing complete")

    # After inference, publish segmentation volume back to the topic:
    #
    #   result_path = job_dir / "output" / "neuro_seg.nii.gz"
    #   _publish_to_requester(ctx, message, "Publishing result…")
    #   _publish_result_nifti_sync(ctx, topic, result_path)
    #   _publish_to_requester(ctx, message, "Job finished.")


# def _publish_result_nifti_sync(
#     ctx: ServiceProviderContext, topic: str, result_path: Path
# ) -> None:
#     import asyncio
#
#     future = asyncio.run_coroutine_threadsafe(
#         ctx.publish_nifti_send(topic, str(result_path)),
#         ctx.loop,
#     )
#     http_status = future.result(timeout=120.0)
#     LOGGER.info("NEURO_SEG: published result HTTP %s", http_status)


def on_nifti_send(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    _handle_inbound_nifti_send(
        ctx, message, input_dir, file_count, total_bytes
    )


HANDLERS = ServiceProviderHandlers(
    on_nifti_send=on_nifti_send,
    on_send_download_start=on_send_download_start,
    build_status_response=build_status_response,
)


if __name__ == "__main__":
    run_sync(
        ServiceProviderConfig(
            product_name=PRODUCT_NAME,
            events=["nifti-send", "status-request"],
        ),
        HANDLERS,
    )
