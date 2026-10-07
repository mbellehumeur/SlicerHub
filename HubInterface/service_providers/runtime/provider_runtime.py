"""Helpers for resource-server onMessage scripts (publish, dicom-send payloads)."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from hub_client import (
    HUB_CLIENT_HTTP_PAYLOAD_MAX_CONCURRENT,
    HUB_CLIENT_HTTP_PAYLOAD_PROGRESS_INTERVAL,
    HubPayloadTruncatedError,
    _PayloadHttpConnectionPool,
    _normalize_loopback_url,
    _payload_chunk_plan,
    _reassemble_file_chunks,
    generate_message_id,
)

if TYPE_CHECKING:
    from resource_server_hub import ResourceServerHubConnection

LOGGER = logging.getLogger("HubInterface.ResourceServerRuntime")
LOGGER.setLevel(logging.INFO)

_connections: Dict[str, "ResourceServerHubConnection"] = {}
_receive_log: List[Dict[str, Any]] = []


def register_connection(
    product_name: str, connection: "ResourceServerHubConnection"
) -> None:
    key = (product_name or "").strip()
    if key:
        _connections[key] = connection


def unregister_connection(product_name: str) -> None:
    key = (product_name or "").strip()
    if key:
        _connections.pop(key, None)


def get_receive_log() -> List[Dict[str, Any]]:
    return list(_receive_log)


def get_active_resource_server_products() -> List[str]:
    """Product names of resource servers currently connected to the hub."""
    return list(_connections.keys())


def _dicom_bytes_from_resource(resource: Dict[str, Any]) -> Optional[bytes]:
    data = resource.get("data")
    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
        return bytes(data)
    if isinstance(data, str) and data.strip():
        try:
            raw = base64.standard_b64decode(data)
        except (binascii.Error, ValueError):
            return None
        return raw if raw else None
    return None


def _resolve_hub_connection(
    product_name: Optional[str],
) -> Optional["ResourceServerHubConnection"]:
    key = (product_name or "").strip()
    if key and key in _connections:
        return _connections[key]
    if len(_connections) == 1:
        return next(iter(_connections.values()))
    if _connections:
        LOGGER.warning(
            "Ambiguous Slicer hub connection for product=%s (active: %s)",
            key or "(none)",
            ", ".join(_connections.keys()),
        )
    return None


@dataclass(frozen=True)
class _SendFileJob:
    index: int
    file_name: str
    url: str
    bearer_token: str
    expected_byte_length: Optional[int] = None
    chunk_index: int = 0


@dataclass(frozen=True)
class _SendFileJobToDisk:
    job: _SendFileJob
    dest_path: Path


def _entry_expected_byte_length(entry: Dict[str, Any]) -> Optional[int]:
    bl = entry.get("byteLength")
    if isinstance(bl, int) and bl >= 0:
        return bl
    return None


def _expected_binary_batch_bytes(files: List[Any]) -> int:
    total = 0
    for entry in files:
        if not isinstance(entry, dict):
            continue
        bl = _entry_expected_byte_length(entry)
        if bl is not None:
            total += bl
    return total


def _append_hub_payload_jobs(
    jobs: List[_SendFileJob],
    *,
    index: int,
    file_name: str,
    entry: Dict[str, Any],
    client: Any,
    hub_token: str,
) -> bool:
    """Append one job per chunk when ``payloadIds[]`` (or legacy ``payloadId``) is pending."""
    payload_ids, chunk_lengths = _payload_chunk_plan(entry)
    if not payload_ids:
        return False
    for chunk_idx, payload_id in enumerate(payload_ids):
        payload_url = client._resolve_payload_url(payload_id)
        if not payload_url:
            LOGGER.warning(
                "Invalid payloadId index=%d chunk=%d file=%s payloadId=%s",
                index,
                chunk_idx,
                file_name,
                payload_id[:16],
            )
            continue
        expected = (
            chunk_lengths[chunk_idx] if chunk_idx < len(chunk_lengths) else None
        )
        jobs.append(
            _SendFileJob(
                index=index,
                file_name=file_name,
                url=payload_url,
                bearer_token=hub_token,
                expected_byte_length=expected,
                chunk_index=chunk_idx,
            )
        )
    return bool(payload_ids)


def _reassemble_send_chunk_downloads(
    chunk_results: List[Tuple[int, int, str, bytes]],
    expected_by_index: Dict[int, Optional[int]],
) -> List[Tuple[int, str, bytes]]:
    by_file: Dict[int, List[Tuple[int, bytes]]] = {}
    names: Dict[int, str] = {}
    for index, chunk_index, file_name, data in chunk_results:
        by_file.setdefault(index, []).append((chunk_index, data))
        names[index] = file_name
    assembled: List[Tuple[int, str, bytes]] = []
    for index in sorted(by_file.keys()):
        chunk_pairs = sorted(by_file[index], key=lambda item: item[0])
        data = _reassemble_file_chunks(
            [chunk for _chunk_index, chunk in chunk_pairs],
            expected_by_index.get(index),
        )
        assembled.append((index, names[index], data))
    return assembled


def _expected_byte_length_by_index(files: List[Any]) -> Dict[int, Optional[int]]:
    return {
        idx: _entry_expected_byte_length(entry)
        for idx, entry in enumerate(files)
        if isinstance(entry, dict)
    }


def _binary_batch_dest_path(
    output_dir: Path, file_name: str, index: int, hub_event: str
) -> Path:
    name = os.path.basename(file_name.strip())
    if not name:
        if hub_event.startswith("nifti"):
            name = "nifti-send.nii.gz" if index == 0 else f"nifti-send-{index + 1}.nii.gz"
        else:
            name = f"dicom-send-{index + 1}.dcm"
    return output_dir / name


def _write_binary_batch_bytes_to_dir(
    output_dir: Path, file_name: str, data: bytes, dest_path: Path
) -> int:
    """Write one binary batch file entry to ``output_dir`` (expands zip archives in place)."""
    name = os.path.basename(file_name.strip()) or dest_path.name
    if name.lower().endswith(".zip"):
        zip_path = dest_path if dest_path.suffix.lower() == ".zip" else output_dir / name
        zip_path.write_bytes(data)
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(output_dir)
            zip_path.unlink(missing_ok=True)
        except zipfile.BadZipFile:
            LOGGER.warning("Invalid zip payload: %s", zip_path)
            return 0
        return len(data)

    dest_path.write_bytes(data)
    return len(data)


def _download_send_file_job(job: _SendFileJob) -> Tuple[int, int, str, bytes, float]:
    """Blocking GET on a worker thread (``http.client`` pool, one connection per thread)."""
    pool = _PayloadHttpConnectionPool()
    url = _normalize_loopback_url(job.url)
    started = time.monotonic()
    data = pool.download_with_retry(
        url, job.bearer_token, job.expected_byte_length
    )
    return (
        job.index,
        job.chunk_index,
        job.file_name,
        data,
        max(time.monotonic() - started, 0.0),
    )


def _download_send_chunk_to_disk(
    job_to_disk: _SendFileJobToDisk,
) -> Tuple[int, int, str, bytes, Path, float]:
    """Download one chunk on the worker thread (write deferred until reassembly)."""
    job = job_to_disk.job
    pool = _PayloadHttpConnectionPool()
    url = _normalize_loopback_url(job.url)
    started = time.monotonic()
    data = pool.download_with_retry(
        url, job.bearer_token, job.expected_byte_length
    )
    return (
        job.index,
        job.chunk_index,
        job.file_name,
        data,
        job_to_disk.dest_path,
        max(time.monotonic() - started, 0.0),
    )


def _parallel_download_send_files_to_dir(
    jobs: List[_SendFileJobToDisk],
    *,
    msg_id: str,
    hub_event: str,
    expected_by_index: Dict[int, Optional[int]],
) -> List[Tuple[int, str, int]]:
    if not jobs:
        return []

    max_concurrent = HUB_CLIENT_HTTP_PAYLOAD_MAX_CONCURRENT
    if max_concurrent <= 0:
        max_concurrent = len(jobs)
    else:
        max_concurrent = max(1, min(max_concurrent, len(jobs)))

    progress_interval = HUB_CLIENT_HTTP_PAYLOAD_PROGRESS_INTERVAL
    total_jobs = len(jobs)
    started_batch = time.monotonic()
    sample_url = _normalize_loopback_url(jobs[0].job.url)
    LOGGER.info(
        "Hub send file batch start id=%s event=%s files=%d concurrent=%d "
        "stream_to_disk=true url_sample=%s",
        msg_id,
        hub_event,
        total_jobs,
        max_concurrent,
        sample_url,
    )

    completed_count = 0
    completed_bytes = 0
    progress_lock = threading.Lock()
    first_failure: Optional[BaseException] = None

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(_download_send_chunk_to_disk, job): job for job in jobs
        }
        chunk_results: List[Tuple[int, int, str, bytes, Path]] = []
        for future in as_completed(futures):
            job_to_disk = futures[future]
            job = job_to_disk.job
            try:
                index, chunk_index, file_name, data, dest_path, _elapsed = future.result()
            except Exception as exc:
                LOGGER.error(
                    "Hub send file GET failed id=%s event=%s index=%s chunk=%s "
                    "file=%s url=%s dest=%s: %s",
                    msg_id,
                    hub_event,
                    job.index,
                    job.chunk_index,
                    job.file_name,
                    _normalize_loopback_url(job.url),
                    job_to_disk.dest_path,
                    exc,
                )
                if first_failure is None:
                    first_failure = exc
                continue

            with progress_lock:
                completed_count += 1
                completed_bytes += len(data)
                if progress_interval > 0 and (
                    completed_count % progress_interval == 0
                    or completed_count == total_jobs
                ):
                    LOGGER.info(
                        "Hub send file batch progress id=%s event=%s "
                        "completed=%d/%d bytes=%d elapsed=%.2fs",
                        msg_id,
                        hub_event,
                        completed_count,
                        total_jobs,
                        completed_bytes,
                        max(time.monotonic() - started_batch, 0.0),
                    )
            chunk_results.append((index, chunk_index, file_name, data, dest_path))

    if first_failure is not None and len(chunk_results) < total_jobs:
        raise first_failure

    dest_by_index = {
        index: dest_path for index, _ci, _fn, _data, dest_path in chunk_results
    }
    assembled = _reassemble_send_chunk_downloads(
        [(i, ci, fn, data) for i, ci, fn, data, _dest in chunk_results],
        expected_by_index,
    )
    results: List[Tuple[int, str, int]] = []
    for index, file_name, data in assembled:
        dest_path = dest_by_index[index]
        byte_length = _write_binary_batch_bytes_to_dir(
            dest_path.parent, file_name, data, dest_path
        )
        results.append((index, file_name, byte_length))

    results.sort(key=lambda item: item[0])
    LOGGER.info(
        "Hub send file batch done id=%s event=%s files=%d bytes=%d elapsed=%.2fs "
        "concurrent=%d stream_to_disk=true",
        msg_id,
        hub_event,
        len(results),
        sum(length for _, _, length in results),
        max(time.monotonic() - started_batch, 0.0),
        max_concurrent,
    )
    return results


def _parallel_download_send_files(
    jobs: List[_SendFileJob],
    *,
    msg_id: str,
    hub_event: str,
    expected_by_index: Dict[int, Optional[int]],
) -> List[Tuple[int, str, bytes]]:
    if not jobs:
        return []

    max_concurrent = HUB_CLIENT_HTTP_PAYLOAD_MAX_CONCURRENT
    if max_concurrent <= 0:
        max_concurrent = len(jobs)
    else:
        max_concurrent = max(1, min(max_concurrent, len(jobs)))

    progress_interval = HUB_CLIENT_HTTP_PAYLOAD_PROGRESS_INTERVAL
    total_jobs = len(jobs)
    started_batch = time.monotonic()
    sample_url = _normalize_loopback_url(jobs[0].url)
    LOGGER.info(
        "Hub send file batch start id=%s event=%s files=%d concurrent=%d url_sample=%s",
        msg_id,
        hub_event,
        total_jobs,
        max_concurrent,
        sample_url,
    )

    results: List[Tuple[int, str, bytes]] = []
    completed_count = 0
    completed_bytes = 0
    progress_lock = threading.Lock()
    first_failure: Optional[BaseException] = None
    chunk_results: List[Tuple[int, int, str, bytes]] = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(_download_send_file_job, job): job for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                index, chunk_index, file_name, data, _elapsed = future.result()
            except Exception as exc:
                LOGGER.error(
                    "Hub send file GET failed id=%s event=%s index=%s chunk=%s "
                    "file=%s url=%s: %s",
                    msg_id,
                    hub_event,
                    job.index,
                    job.chunk_index,
                    job.file_name,
                    _normalize_loopback_url(job.url),
                    exc,
                )
                if first_failure is None:
                    first_failure = exc
                continue

            with progress_lock:
                completed_count += 1
                completed_bytes += len(data)
                if progress_interval > 0 and (
                    completed_count % progress_interval == 0
                    or completed_count == total_jobs
                ):
                    LOGGER.info(
                        "Hub send file batch progress id=%s event=%s "
                        "completed=%d/%d bytes=%d elapsed=%.2fs",
                        msg_id,
                        hub_event,
                        completed_count,
                        total_jobs,
                        completed_bytes,
                        max(time.monotonic() - started_batch, 0.0),
                    )
            chunk_results.append((index, chunk_index, file_name, data))

    if first_failure is not None and len(chunk_results) < total_jobs:
        raise first_failure

    results = _reassemble_send_chunk_downloads(chunk_results, expected_by_index)
    LOGGER.info(
        "Hub send file batch done id=%s event=%s files=%d bytes=%d elapsed=%.2fs "
        "concurrent=%d",
        msg_id,
        hub_event,
        len(results),
        sum(len(data) for _, _, data in results),
        max(time.monotonic() - started_batch, 0.0),
        max_concurrent,
    )
    return results


def _plan_send_file_jobs(
    files: List[Any],
    product_name: Optional[str],
    file_name_for_index: Callable[[Dict[str, Any], int], str],
) -> Tuple[List[Tuple[int, str, bytes]], List[_SendFileJob]]:
    """Split ``context.files[]`` into inline bytes vs parallel HTTP jobs."""
    inline: List[Tuple[int, str, bytes]] = []
    jobs: List[_SendFileJob] = []
    connection = _resolve_hub_connection(product_name)
    client = connection._client if connection else None
    hub_token = client._token or "" if client else ""

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            continue
        file_name = file_name_for_index(entry, index)
        expected = _entry_expected_byte_length(entry)
        raw = _dicom_bytes_from_resource(entry)
        if raw:
            if expected is not None and len(raw) != expected:
                raise HubPayloadTruncatedError(
                    f"inline payload index={index} file={file_name}: "
                    f"expected={expected} received={len(raw)}"
                )
            inline.append((index, file_name, raw))
            continue

        url = entry.get("url")
        if isinstance(url, str) and url.strip().startswith(("http://", "https://")):
            jobs.append(
                _SendFileJob(
                    index=index,
                    file_name=file_name,
                    url=url.strip(),
                    bearer_token="",
                    expected_byte_length=expected,
                )
            )
            continue

        payload_ids, _chunk_lengths = _payload_chunk_plan(entry)
        if payload_ids and entry.get("data") is None:
            if client is None:
                LOGGER.warning(
                    "No Slicer hub client for payloadIds index=%d file=%s (active: %s)",
                    index,
                    file_name,
                    ", ".join(get_active_resource_server_products()) or "(none)",
                )
                continue
            _append_hub_payload_jobs(
                jobs,
                index=index,
                file_name=file_name,
                entry=entry,
                client=client,
                hub_token=hub_token,
            )

    return inline, jobs


def _plan_send_file_jobs_to_dir(
    files: List[Any],
    product_name: Optional[str],
    file_name_for_index: Callable[[Dict[str, Any], int], str],
    output_dir: Path,
    hub_event: str,
) -> Tuple[List[Tuple[int, str, Path, bytes]], List[_SendFileJobToDisk]]:
    """Split ``context.files[]`` into inline bytes vs parallel on-disk HTTP jobs."""
    inline: List[Tuple[int, str, Path, bytes]] = []
    jobs: List[_SendFileJobToDisk] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = _resolve_hub_connection(product_name)
    client = connection._client if connection else None
    hub_token = client._token or "" if client else ""

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            continue
        file_name = file_name_for_index(entry, index)
        dest_path = _binary_batch_dest_path(output_dir, file_name, index, hub_event)
        expected = _entry_expected_byte_length(entry)
        raw = _dicom_bytes_from_resource(entry)
        if raw:
            if expected is not None and len(raw) != expected:
                raise HubPayloadTruncatedError(
                    f"inline payload index={index} file={file_name}: "
                    f"expected={expected} received={len(raw)}"
                )
            inline.append((index, file_name, dest_path, raw))
            continue

        url = entry.get("url")
        if isinstance(url, str) and url.strip().startswith(("http://", "https://")):
            jobs.append(
                _SendFileJobToDisk(
                    job=_SendFileJob(
                        index=index,
                        file_name=file_name,
                        url=url.strip(),
                        bearer_token="",
                        expected_byte_length=expected,
                    ),
                    dest_path=dest_path,
                )
            )
            continue

        payload_ids, _chunk_lengths = _payload_chunk_plan(entry)
        if payload_ids and entry.get("data") is None:
            if client is None:
                LOGGER.warning(
                    "No Slicer hub client for payloadIds index=%d file=%s (active: %s)",
                    index,
                    file_name,
                    ", ".join(get_active_resource_server_products()) or "(none)",
                )
                continue
            chunk_jobs: List[_SendFileJob] = []
            _append_hub_payload_jobs(
                chunk_jobs,
                index=index,
                file_name=file_name,
                entry=entry,
                client=client,
                hub_token=hub_token,
            )
            for chunk_job in chunk_jobs:
                jobs.append(_SendFileJobToDisk(job=chunk_job, dest_path=dest_path))

    return inline, jobs


def _extract_all_send_files_to_dir(
    message: Dict[str, Any],
    product_name: Optional[str],
    hub_event: str,
    file_name_for_index: Callable[[Dict[str, Any], int], str],
    output_dir: Path,
) -> Tuple[int, int]:
    """Download ``context.files[]`` in parallel and stream each file to ``output_dir``."""
    event = message.get("event") or {}
    if not isinstance(event, dict) or event.get("hub.event") != hub_event:
        return 0, 0

    context = event.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("files"), list):
        return 0, 0

    files = context["files"]
    msg_id = str(message.get("id") or "")
    total_files = len(files)
    output_dir = Path(output_dir)
    LOGGER.info(
        "extract_all_%s_files_to_dir id=%s files=%d product=%s dir=%s",
        hub_event,
        msg_id,
        total_files,
        (product_name or "").strip() or "(none)",
        output_dir,
    )

    inline, jobs = _plan_send_file_jobs_to_dir(
        files, product_name, file_name_for_index, output_dir, hub_event
    )

    total_bytes = 0
    resolved = 0
    for _index, file_name, dest_path, raw in inline:
        total_bytes += _write_binary_batch_bytes_to_dir(output_dir, file_name, raw, dest_path)
        resolved += 1

    if jobs:
        downloaded = _parallel_download_send_files_to_dir(
            jobs,
            msg_id=msg_id,
            hub_event=hub_event,
            expected_by_index=_expected_byte_length_by_index(files),
        )
        for _index, _file_name, byte_length in downloaded:
            total_bytes += byte_length
            resolved += 1

    expected = sum(1 for entry in files if isinstance(entry, dict))
    if resolved < expected:
        LOGGER.warning(
            "extract_all_%s_files_to_dir id=%s resolved=%d/%d (missing sources)",
            hub_event,
            msg_id,
            resolved,
            expected,
        )

    expected_bytes = _expected_binary_batch_bytes(files)
    if expected_bytes > 0 and total_bytes != expected_bytes:
        raise HubPayloadTruncatedError(
            f"extract_all_{hub_event}_files_to_dir id={msg_id}: "
            f"expected={expected_bytes} bytes={total_bytes}"
        )

    LOGGER.info(
        "extract_all_%s_files_to_dir done id=%s resolved=%d/%d bytes=%d "
        "inline=%d downloaded=%d dir=%s",
        hub_event,
        msg_id,
        resolved,
        total_files,
        total_bytes,
        len(inline),
        len(jobs),
        output_dir,
    )
    return resolved, total_bytes


def _extract_all_send_payloads(
    message: Dict[str, Any],
    product_name: Optional[str],
    hub_event: str,
    file_name_for_index: Callable[[Dict[str, Any], int], str],
) -> List[Tuple[str, bytes]]:
    event = message.get("event") or {}
    if not isinstance(event, dict) or event.get("hub.event") != hub_event:
        return []

    context = event.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("files"), list):
        return []

    files = context["files"]
    msg_id = str(message.get("id") or "")
    total_files = len(files)
    LOGGER.info(
        "extract_all_%s_payloads id=%s files=%d product=%s",
        hub_event,
        msg_id,
        total_files,
        (product_name or "").strip() or "(none)",
    )

    inline, jobs = _plan_send_file_jobs(files, product_name, file_name_for_index)
    downloaded: List[Tuple[int, str, bytes]] = []
    if jobs:
        downloaded = _parallel_download_send_files(
            jobs,
            msg_id=msg_id,
            hub_event=hub_event,
            expected_by_index=_expected_byte_length_by_index(files),
        )

    merged = [(index, name, data) for index, name, data in inline]
    merged.extend(downloaded)
    merged.sort(key=lambda item: item[0])

    expected = sum(1 for entry in files if isinstance(entry, dict))
    if len(merged) < expected:
        LOGGER.warning(
            "extract_all_%s_payloads id=%s resolved=%d/%d (missing sources)",
            hub_event,
            msg_id,
            len(merged),
            expected,
        )

    payloads = [(name, data) for _, name, data in merged]
    total_bytes = sum(len(data) for _, data in payloads)
    expected_bytes = _expected_binary_batch_bytes(files)
    if expected_bytes > 0 and total_bytes != expected_bytes:
        raise HubPayloadTruncatedError(
            f"extract_all_{hub_event}_payloads id={msg_id}: "
            f"expected={expected_bytes} bytes={total_bytes}"
        )
    LOGGER.info(
        "extract_all_%s_payloads done id=%s resolved=%d/%d bytes=%d "
        "inline=%d downloaded=%d",
        hub_event,
        msg_id,
        len(payloads),
        total_files,
        total_bytes,
        len(inline),
        len(downloaded),
    )
    return payloads


def _file_name_from_resource(resource: Dict[str, Any], index: int) -> str:
    name = resource.get("fileName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"dicom-send-{index + 1}.dcm"


def _file_name_from_batch_entry(entry: Dict[str, Any], index: int) -> str:
    name = entry.get("fileName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"dicom-send-{index + 1}.dcm"


def extract_all_dicom_send_payloads(
    message: Dict[str, Any],
    product_name: Optional[str] = None,
) -> List[Tuple[str, bytes]]:
    """Return every ``(fileName, raw bytes)`` in a ``dicom-send`` message."""
    return _extract_all_send_payloads(
        message,
        product_name,
        "dicom-send",
        _file_name_from_batch_entry,
    )


def extract_all_dicom_send_files_to_dir(
    message: Dict[str, Any],
    output_dir: Path,
    product_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Stream every ``dicom-send`` file in ``context.files[]`` into ``output_dir``."""
    return _extract_all_send_files_to_dir(
        message,
        product_name,
        "dicom-send",
        _file_name_from_batch_entry,
        Path(output_dir),
    )


def _nifti_file_name_from_resource(resource: Dict[str, Any], index: int) -> str:
    name = resource.get("fileName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if index == 0:
        return "nifti-send.nii.gz"
    return f"nifti-send-{index + 1}.nii.gz"


def extract_all_nifti_send_payloads(
    message: Dict[str, Any],
    product_name: Optional[str] = None,
) -> List[Tuple[str, bytes]]:
    """Return every ``(fileName, raw bytes)`` in a ``nifti-send`` context list."""
    return _extract_all_send_payloads(
        message,
        product_name,
        "nifti-send",
        _nifti_file_name_from_resource,
    )


def extract_all_nifti_send_files_to_dir(
    message: Dict[str, Any],
    output_dir: Path,
    product_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Stream every ``nifti-send`` file in ``context.files[]`` into ``output_dir``."""
    return _extract_all_send_files_to_dir(
        message,
        product_name,
        "nifti-send",
        _nifti_file_name_from_resource,
        Path(output_dir),
    )


def extract_dicom_send_payload(
    message: Dict[str, Any],
    product_name: Optional[str] = None,
) -> Optional[Tuple[str, bytes]]:
    """Return the first ``(fileName, raw bytes)`` from a ``dicom-send`` message."""
    payloads = extract_all_dicom_send_payloads(message, product_name)
    if not payloads:
        return None
    return payloads[0]


def record_dicom_send_received(topic: str, byte_length: int) -> Dict[str, Any]:
    entry = {
        "topic": topic,
        "size": byte_length,
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _receive_log.append(entry)
    return entry


def record_nifti_send_received(topic: str, byte_length: int) -> Dict[str, Any]:
    entry = {
        "topic": topic,
        "event": "nifti-send",
        "size": byte_length,
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _receive_log.append(entry)
    return entry


def build_dicom_send_publish_message(
    topic: str, file_path: str
) -> Dict[str, Any]:
    path = os.path.normpath(file_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"DICOM file not found: {path}")

    with open(path, "rb") as dcm_file:
        raw = dcm_file.read()

    file_name = os.path.basename(path)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "id": generate_message_id(),
        "event": {
            "hub.topic": topic,
            "hub.event": "dicom-send",
            "context": {
                "files": [
                    {
                        "fileName": file_name,
                        "mimeType": "application/dicom",
                        "byteLength": len(raw),
                        "data": raw,
                    }
                ]
            },
        },
    }


def build_nifti_send_publish_message(
    topic: str, file_path: str
) -> Dict[str, Any]:
    path = os.path.normpath(file_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"NIfTI file not found: {path}")

    with open(path, "rb") as nifti_file:
        raw = nifti_file.read()

    file_name = os.path.basename(path)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "id": generate_message_id(),
        "event": {
            "hub.topic": topic,
            "hub.event": "nifti-send",
            "context": {
                "files": [
                    {
                        "fileName": file_name,
                        "mimeType": "application/vnd.unknown.nifti-1",
                        "byteLength": len(raw),
                        "data": raw,
                    }
                ]
            },
        },
    }


def send_hub_request_response(
    product_name: str,
    correlation_id: str,
    data_type: str,
    data: Dict[str, Any],
    topic: Optional[str] = None,
) -> bool:
    """Send ``<datatype>-response`` on the resource server hub WebSocket."""
    key = (product_name or "").strip()
    connection = _connections.get(key)
    if not connection or not connection._client or not connection._loop:
        LOGGER.warning(
            "send_hub_request_response: no connection for product=%s (active: %s)",
            key,
            ", ".join(get_active_resource_server_products()) or "(none)",
        )
        return False

    def run() -> None:
        connection._client.send_hub_request_response(
            correlation_id, data_type, data, topic
        )

    connection._loop.call_soon_threadsafe(run)
    return True


def publish_dicom_send_file(product_name: str, topic: str, file_path: str) -> bool:
    """Schedule dicom-send publish on the resource server's hub connection thread."""
    key = (product_name or "").strip()
    connection = _connections.get(key)
    if not connection:
        LOGGER.warning(
            "publish_dicom_send_file: no connection for product=%s (active: %s)",
            key,
            ", ".join(get_active_resource_server_products()) or "(none)",
        )
        return False

    try:
        message = build_dicom_send_publish_message(topic, file_path)
    except Exception as exc:
        LOGGER.exception("publish_dicom_send_file build failed: %s", exc)
        return False

    connection.schedule_publish(message)
    return True


def publish_nifti_send_file(product_name: str, topic: str, file_path: str) -> bool:
    """Schedule nifti-send publish on the resource server's hub connection thread."""
    key = (product_name or "").strip()
    connection = _connections.get(key)
    if not connection:
        LOGGER.warning(
            "publish_nifti_send_file: no connection for product=%s (active: %s)",
            key,
            ", ".join(get_active_resource_server_products()) or "(none)",
        )
        return False

    try:
        message = build_nifti_send_publish_message(topic, file_path)
    except Exception as exc:
        LOGGER.exception("publish_nifti_send_file build failed: %s", exc)
        return False

    connection.schedule_publish(message)
    return True


def build_status_update_publish_message(
    topic: str,
    target_subscriber_name: str,
    message: str,
    level: str = "info",
) -> Dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        raise ValueError("status-update message is empty")
    target = str(target_subscriber_name or "").strip()
    if not target:
        raise ValueError("status-update target subscriber is empty")
    topic_name = str(topic or "").strip()
    if not topic_name:
        raise ValueError("status-update topic is empty")
    level_name = str(level or "info").strip().lower() or "info"
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "id": generate_message_id(),
        "target.subscriber.name": target,
        "event": {
            "hub.topic": topic_name,
            "hub.event": "status-update",
            "context": {
                "message": text,
                "level": level_name,
            },
        },
    }


IDC_CLAUDE_SEND_EVENT = "idc-claude-send"


def build_idc_claude_send_publish_message(
    topic: str,
    target_subscriber_name: str,
    correlation_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Publish ``idc-claude-send`` result JSON to a single worklist subscriber."""
    target = str(target_subscriber_name or "").strip()
    if not target:
        raise ValueError("idc-claude-send target subscriber is empty")
    topic_name = str(topic or "").strip()
    if not topic_name:
        raise ValueError("idc-claude-send topic is empty")
    corr = str(correlation_id or "").strip()
    if not corr:
        raise ValueError("idc-claude-send correlation id is empty")
    if not isinstance(payload, dict):
        raise ValueError("idc-claude-send payload must be a dict")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "id": generate_message_id(),
        "target.subscriber.name": target,
        "event": {
            "hub.topic": topic_name,
            "hub.event": IDC_CLAUDE_SEND_EVENT,
            "context": {
                "id": corr,
                "dataType": "IDC-CLAUDE",
                "data": payload,
            },
        },
    }


def publish_idc_claude_send(
    product_name: str,
    topic: str,
    target_subscriber_name: str,
    correlation_id: str,
    payload: Dict[str, Any],
) -> bool:
    """Schedule ``idc-claude-send`` publish (detached job result) to the requester."""
    key = (product_name or "").strip()
    connection = _connections.get(key)
    if not connection:
        LOGGER.warning(
            "publish_idc_claude_send: no connection for product=%s (active: %s)",
            key,
            ", ".join(get_active_resource_server_products()) or "(none)",
        )
        return False
    try:
        message = build_idc_claude_send_publish_message(
            topic, target_subscriber_name, correlation_id, payload
        )
    except Exception as exc:
        LOGGER.warning("publish_idc_claude_send build failed: %s", exc)
        return False
    connection.schedule_publish(message)
    return True


def publish_status_update(
    product_name: str,
    topic: str,
    target_subscriber_name: str,
    message: str,
    level: str = "info",
) -> bool:
    """Schedule status-update publish to a single subscriber on the hub topic."""
    key = (product_name or "").strip()
    connection = _connections.get(key)
    if not connection:
        LOGGER.warning(
            "publish_status_update: no connection for product=%s (active: %s)",
            key,
            ", ".join(get_active_resource_server_products()) or "(none)",
        )
        return False

    try:
        hub_message = build_status_update_publish_message(
            topic, target_subscriber_name, message, level
        )
    except Exception as exc:
        LOGGER.warning("publish_status_update build failed: %s", exc)
        return False

    connection.schedule_publish(hub_message)
    return True
