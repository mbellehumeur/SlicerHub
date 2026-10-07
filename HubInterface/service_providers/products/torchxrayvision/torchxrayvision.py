#!/usr/bin/env python3
"""TorchXRayVision Hub service provider — Slicer onMessage + standalone CLI.

Slicer Slicer Hub (Service Providers): point the onMessage script path at this file.

Standalone (plain Python, no Slicer UI) from ``HubInterface/``:

    pip install -e python_client
    pip install aiohttp
    # Optional: TotalSegmentator + torch in this Python, or PythonSlicer on PATH
    python service_providers/products/torchxrayvision/torchxrayvision.py --local

Default hub is SLICER-HUB-CLOUD; ``--local`` uses ``http://127.0.0.1:2018``.

TODO: replace the TotalSegmentator inference below with torchxrayvision chest
X-ray classification (https://github.com/mlmed/torchxrayvision). Until then this
product behaves exactly like ``total_segmentator.py`` under product name
``TORCHXRAYVISION``.

Why the Slicer onMessage path is safe (cross-references to ``Lib/service_provider_hub.py``):

- For ``dicom-send`` and ``nifti-send``, ``_dispatch_service_provider_on_message``
  already invokes the handler via ``asyncio.to_thread`` (one worker thread),
  off both the hub asyncio loop and the Slicer Qt UI thread.
- The hub does **not** call ``fetch_all_payloads`` before those events; bytes are
  streamed in parallel (25 concurrent GETs by default) directly into the job
  ``input/`` directory via ``extract_all_*_send_files_to_dir``.
- The hub message loop ``await``s one handler at a time per provider, so
  ``onMessage`` calls for one provider are naturally serialized.

Inference runs in a **subprocess** (``PythonSlicer`` + TotalSegmentator CLI when
available, else ``python -m totalsegmentator``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_SP_ROOT = _SCRIPT_DIR.parent.parent
for _extra in (
    _SCRIPT_DIR,
    _SP_ROOT,
    _SP_ROOT / "runtime",
    _SP_ROOT.parent / "python_client" / "src",
):
    _extra_str = str(_extra)
    if _extra.is_dir() and _extra_str not in sys.path:
        sys.path.insert(0, _extra_str)

from provider_runtime import (
    HubPayloadTruncatedError,
    extract_all_dicom_send_files_to_dir,
    extract_all_nifti_send_files_to_dir,
    get_active_service_provider_products,
    publish_dicom_send_file,
    publish_status_update,
    record_dicom_send_received,
    record_nifti_send_received,
)
from hub_client import binary_batch_files_pending_stats

_LOG_PREFIX = "TorchXRayVision"
DEFAULT_PRODUCT_NAME = "TORCHXRAYVISION"
_job_serial = 0
_job_status_context: Dict[str, str] = {}


def _format_message(message: str, *args: Any) -> str:
    return message % args if args else message


def _on_slicer_main_thread(fn: Any) -> None:
    """Run ``fn`` on the Qt GUI thread (safe for ``showConsoleMessage``)."""
    try:
        import qt

        qt.QTimer.singleShot(0, fn)
    except ImportError:
        fn()


def _start_job(topic: str, product_name: str, target_subscriber: str) -> int:
    global _job_serial, _job_status_context
    _job_serial += 1
    job_number = _job_serial
    _job_status_context = {
        "topic": (topic or "").strip(),
        "product_name": (product_name or DEFAULT_PRODUCT_NAME).strip()
        or DEFAULT_PRODUCT_NAME,
        "target_subscriber": (target_subscriber or "").strip(),
        "job_number": str(job_number),
    }
    return job_number


def _clear_job_status_context() -> None:
    global _job_status_context
    _job_status_context = {}


def _job_prefix() -> str:
    job_number = _job_status_context.get("job_number", "").strip()
    return f"Job #{job_number}: " if job_number else ""


def _status_clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _format_status_line(text: str) -> str:
    return f"[{_status_clock()}] {_job_prefix()}{text}"


def _emit_status_update(line: str, level: str = "info") -> None:
    ctx = _job_status_context
    target = ctx.get("target_subscriber", "")
    topic = ctx.get("topic", "")
    if not target or not topic:
        return
    publish_status_update(
        ctx.get("product_name", DEFAULT_PRODUCT_NAME),
        topic,
        target,
        line,
        level,
    )


def _debug_log(message: str, *args: Any) -> None:
    """Slicer console only (not sent as status-update)."""
    text = _format_message(message, *args).rstrip()
    if text:
        print(f"{_LOG_PREFIX}: {text}")


def _show_console_error(line: str) -> None:
    def _show_red() -> None:
        try:
            import slicer

            slicer.app.showConsoleMessage(line, True)
        except ImportError:
            print(line, file=sys.stderr)

    try:
        import slicer  # noqa: F401

        _on_slicer_main_thread(_show_red)
    except ImportError:
        print(line, file=sys.stderr)


def _debug_error(message: str, *args: Any) -> None:
    """Slicer console error only (not sent as status-update)."""
    text = _format_message(message, *args).rstrip()
    if not text:
        return
    _show_console_error(f"{_LOG_PREFIX}: {text}")


def _status_log(message: str, *args: Any) -> None:
    """User-facing progress line (VolView Job Status)."""
    text = _format_message(message, *args).rstrip()
    if not text:
        return
    line = _format_status_line(text)
    print(f"{_LOG_PREFIX}: {line}")
    _emit_status_update(line, "info")


def _status_log_line(line: str, level: str = "info") -> None:
    """User-facing line that is already formatted (e.g. subprocess stdout)."""
    text = line.rstrip()
    if not text:
        return
    full = _format_status_line(text)
    print(f"{_LOG_PREFIX}: {full}")
    _emit_status_update(full, level)


def _status_error(message: str, *args: Any) -> None:
    """User-facing error line (VolView Job Status)."""
    text = _format_message(message, *args).rstrip()
    if not text:
        return
    line = _format_status_line(text)
    _emit_status_update(line, "error")
    _show_console_error(f"{_LOG_PREFIX}: {line}")


def _status_job_finished() -> None:
    """Final job line with wall-clock finish time (VolView Job Status)."""
    if not _job_status_context.get("job_number", "").strip():
        return
    finished_at = _status_clock()
    line = _format_status_line(f"Job finished at {finished_at}")
    print(f"{_LOG_PREFIX}: {line}")
    _emit_status_update(line, "info")


def _status_exception(message: str, *args: Any) -> None:
    _status_error(message, *args)
    for exc_line in traceback.format_exc().splitlines():
        _status_error(exc_line)


def _format_byte_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _entry_file_name(entry: Any, index: int) -> str:
    if isinstance(entry, dict):
        name = str(entry.get("fileName") or "").strip()
        if name:
            return name
    return f"file-{index + 1}"


def _entry_byte_length(entry: Any) -> Optional[int]:
    if not isinstance(entry, dict):
        return None
    byte_length = entry.get("byteLength")
    if isinstance(byte_length, int) and byte_length >= 0:
        return byte_length
    return None


def _format_file_name_span(names: List[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} … {names[-1]}"


def _format_download_started(files: List[Any]) -> str:
    entries = [entry for entry in files if isinstance(entry, dict)]
    if not entries:
        count = len(files)
        return (
            f"Download started ({count} files)"
            if count > 1
            else "Download started"
        )

    names = [_entry_file_name(entry, index) for index, entry in enumerate(entries)]
    lengths = [_entry_byte_length(entry) for entry in entries]
    known_total = sum(length for length in lengths if length is not None)
    unknown_count = sum(1 for length in lengths if length is None)

    if len(entries) == 1:
        name = names[0]
        if lengths[0] is not None:
            return f"Download started: {name} ({_format_byte_size(lengths[0])})"
        return f"Download started: {name}"

    size_part = ""
    if known_total > 0:
        size_part = f", {_format_byte_size(known_total)} total"
    if unknown_count:
        size_part += f" (size unknown for {unknown_count} file(s))"

    return (
        f"Download started: {len(entries)} files{size_part} "
        f"({_format_file_name_span(names)})"
    )


def _format_download_complete(
    files: List[Any], file_count: int, total_bytes: int
) -> str:
    entries = [entry for entry in files if isinstance(entry, dict)]
    size_text = _format_byte_size(total_bytes)

    if file_count == 1:
        name = (
            _entry_file_name(entries[0], 0)
            if entries
            else "1 file"
        )
        return f"Download complete: {name} ({size_text})"

    name_span = _format_file_name_span(
        [_entry_file_name(entry, index) for index, entry in enumerate(entries)]
    )
    if name_span:
        return (
            f"Download complete: {file_count} files, {size_text} ({name_span})"
        )
    return f"Download complete: {file_count} files, {size_text}"


_DICOM_SEND_EVENT = "dicom-send"
_NIFTI_SEND_EVENT = "nifti-send"
_job_busy = False


def build_status_response(provider: Any) -> Dict[str, Any]:
    """Return ``status-response`` payload (``service_provider_hub`` calls this)."""
    product_name = getattr(provider, "product_name", "") or DEFAULT_PRODUCT_NAME
    items: list[Dict[str, str]] = [
        {"key": "availability", "value": "online"},
    ]
    if _job_busy:
        items.append({"key": "job", "value": "running"})
    return {
        "source": "status",
        "product": product_name,
        "items": items,
    }
TS_TASK = "total"
# Job context may override; defaults stay conservative.
# Results are always DICOM SEG published as ``dicom-send`` (SegmentSequence
# carries labels / SNOMED / colors for IRA overlay).
TS_FAST = False
OUTPUT_DICOM_NAME = "segmentations.dcm"


def _context_task_fast(event: Dict[str, Any]) -> Tuple[str, bool]:
    """Read ``task`` / ``fast`` from dicom-send / nifti-send context (IRA options)."""
    ctx = event.get("context") or {}
    if not isinstance(ctx, dict):
        return TS_TASK, TS_FAST
    raw_task = str(ctx.get("task") or "").strip()
    task = raw_task or TS_TASK
    raw_fast = ctx.get("fast")
    if isinstance(raw_fast, bool):
        fast = raw_fast
    elif isinstance(raw_fast, (int, float)):
        fast = bool(raw_fast)
    elif isinstance(raw_fast, str):
        fast = raw_fast.strip().lower() in ("1", "true", "yes", "on")
    else:
        fast = TS_FAST
    return task, fast


def _safe_topic_dir_name(topic: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", topic.strip())
    return safe or "topic"


def _allocate_job_dirs(topic: str) -> Tuple[Path, Path, Path]:
    """Create ``hub-txrv-jobs/<topic>-<stamp>/{input,output}``."""
    stamp = int(time.time() * 1000)
    job_dir = (
        Path(tempfile.gettempdir())
        / "hub-txrv-jobs"
        / f"{_safe_topic_dir_name(topic)}-{stamp}"
    )
    job_input = job_dir / "input"
    job_output = job_dir / "output"
    job_input.mkdir(parents=True, exist_ok=True)
    job_output.mkdir(parents=True, exist_ok=True)
    return job_dir, job_input, job_output


def _redact_message_for_log(message: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a Hub message for logging with inline byte/base64 fields redacted."""

    def _redact_value(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"<bytes len={len(value)}>"
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, item in value.items():
                if key == "data" and item is not None:
                    if isinstance(item, str):
                        out[key] = f"<base64 len={len(item)}>"
                    else:
                        out[key] = _redact_value(item)
                else:
                    out[key] = _redact_value(item)
            return out
        if isinstance(value, list):
            return [_redact_value(item) for item in value]
        return value

    return _redact_value(message)


def onMessage(message: Dict[str, Any], provider: Any) -> None:
    _debug_log(
        "onMessage: received %s",
        json.dumps(_redact_message_for_log(message), default=str),
    )
    event = message.get("event") or {}
    hub_event = event.get("hub.event")
    if hub_event == _NIFTI_SEND_EVENT:
        _on_inbound_send(
            message,
            event,
            provider,
            _NIFTI_SEND_EVENT,
            extract_all_nifti_send_files_to_dir,
            record_nifti_send_received,
            "NIfTI",
        )
        return
    if hub_event != _DICOM_SEND_EVENT:
        return
    _on_inbound_send(
        message,
        event,
        provider,
        _DICOM_SEND_EVENT,
        extract_all_dicom_send_files_to_dir,
        record_dicom_send_received,
        "DICOM",
    )


def _on_inbound_send(
    message: Dict[str, Any],
    event: Dict[str, Any],
    provider: Any,
    hub_event: str,
    extract_files: Callable[..., Tuple[int, int]],
    record_received: Callable[[str, int], None],
    label: str,
) -> None:
    global _job_busy
    topic = (event.get("hub.topic") or "").strip()
    if not topic:
        _debug_error("onMessage: %s missing hub.topic", hub_event)
        return

    product_name = getattr(provider, "product_name", "") or DEFAULT_PRODUCT_NAME
    target_subscriber = str(message.get("subscriber.name") or "").strip()
    _job_busy = True
    _start_job(topic, product_name, target_subscriber)
    files: List[Any] = []
    try:
        raw_files = (event.get("context") or {}).get("files") or []
        if isinstance(raw_files, list):
            files = raw_files
            url_count, payload_files, chunk_count = binary_batch_files_pending_stats(files)
            _debug_log(
                "onMessage: %s download start id=%s topic=%s files=%d "
                "url=%d payloadFiles=%d chunks=%d product=%s",
                hub_event,
                message.get("id", ""),
                topic,
                len(files),
                url_count,
                payload_files,
                chunk_count,
                product_name,
            )
            _status_log(_format_download_started(files))

        job_dir, job_input, job_output = _allocate_job_dirs(topic)
        _debug_log(
            "onMessage: streaming download to %s id=%s topic=%s",
            job_input,
            message.get("id", ""),
            topic,
        )
        try:
            file_count, total_bytes = extract_files(
                message, job_input, product_name
            )
        except HubPayloadTruncatedError as exc:
            _status_error("Download failed: %s", exc)
            _debug_error(
                "onMessage: %s download failed id=%s topic=%s: %s",
                hub_event,
                message.get("id", ""),
                topic,
                exc,
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            return
        if file_count < 1:
            _status_error("Download failed: no %s payload received", label)
            _debug_error(
                "onMessage: no %s payload id=%s topic=%s",
                label,
                message.get("id", ""),
                topic,
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            return
        record_received(topic, total_bytes)
        _status_log(_format_download_complete(files, file_count, total_bytes))
        _debug_log(
            "onMessage: %s id=%s topic=%s files=%d bytes=%d",
            hub_event,
            message.get("id", ""),
            topic,
            file_count,
            total_bytes,
        )

        _run_segmentation_job_body(
            topic,
            product_name,
            job_input,
            job_output,
            job_dir,
            hub_event,
            event,
        )
    finally:
        _job_busy = False
        _status_job_finished()
        _clear_job_status_context()


def _run_segmentation_job_body(
    topic: str,
    product_name: str,
    job_input: Path,
    job_output: Path,
    job_dir: Path,
    hub_event: str,
    event: Optional[Dict[str, Any]] = None,
) -> None:
    output_file: Optional[Path] = None
    try:
        staged_count = _count_input_files(job_input)
        if staged_count < 1:
            _status_error("Segmentation failed: no input files staged")
            _debug_error(
                "no input files for topic=%s in %s",
                topic,
                job_input,
            )
            return

        task, fast = _context_task_fast(event or {})
        _status_log(
            "Segmentation started (task=%s%s)",
            task,
            ", fast" if fast else "",
        )
        _debug_log(
            "starting segmentation topic=%s input=%s files=%d task=%s fast=%s",
            topic,
            job_input,
            staged_count,
            task,
            fast,
        )
        cli_input = _cli_input_path(job_input, hub_event)

        output_file = _run_totalsegmentator(
            cli_input, job_output, hub_event, task=task, fast=fast
        )
        if not output_file:
            _status_error("Segmentation failed: no output produced")
            _debug_error(
                "no output for hub.event=%s topic=%s (job dir kept: %s)",
                hub_event,
                topic,
                job_dir,
            )
            return

        _debug_log(
            "output hub.event=%s topic=%s: %s",
            hub_event,
            topic,
            output_file,
        )
        _status_log("Publishing dicom-send result…")
        published = publish_dicom_send_file(
            product_name, topic, str(output_file)
        )
        if published:
            _debug_log(
                "published %s to topic=%s product=%s",
                output_file,
                topic,
                product_name,
            )
        else:
            _status_error("Failed to publish segmentation result")
            _debug_error(
                "failed to publish %s topic=%s product=%s (active: %s)",
                output_file,
                topic,
                product_name,
                ", ".join(get_active_service_provider_products()) or "(none)",
            )
    except Exception as exc:
        _status_exception("Segmentation failed: %s", exc)
    finally:
        if job_dir and job_dir.is_dir():
            if output_file and output_file.is_file():
                try:
                    shutil.rmtree(job_dir)
                except OSError as exc:
                    _debug_error("cleanup failed: %s", exc)
            else:
                _debug_log("keeping job dir for inspection: %s", job_dir)


def _count_input_files(input_dir: Path) -> int:
    if not input_dir.is_dir():
        return 0
    return sum(
        1
        for path in input_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


def _cli_input_path(input_dir: Path, hub_event: str) -> Path:
    """Resolve TotalSegmentator ``-i`` from Hub ``hub.event``."""
    if hub_event == _NIFTI_SEND_EVENT:
        for pattern in ("*.nii.gz", "*.nii", "*.nhdr", "*.nrrd"):
            matches = sorted(input_dir.glob(pattern))
            if matches:
                if len(matches) > 1:
                    _debug_log(
                        "multiple volume files under %s, using %s",
                        input_dir,
                        matches[0],
                    )
                return matches[0]
        raise FileNotFoundError(f"No NIfTI/volume file under {input_dir}")
    if hub_event == _DICOM_SEND_EVENT:
        return input_dir
    raise ValueError(f"Unsupported hub.event for segmentation: {hub_event!r}")


def _cli_output_path(output_dir: Path, hub_event: str) -> Path:
    """Resolve TotalSegmentator ``-o`` (always DICOM SEG path for Hub publish)."""
    if hub_event in (_NIFTI_SEND_EVENT, _DICOM_SEND_EVENT):
        return output_dir / OUTPUT_DICOM_NAME
    raise ValueError(f"Unsupported hub.event for segmentation: {hub_event!r}")


def _ts_executable_name(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def _total_segmentator_script_candidates() -> list[str]:
    """Locate TotalSegmentator console script (system + user Scripts dirs)."""
    names = {_ts_executable_name("TotalSegmentator")}
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path and path not in seen and os.path.isfile(path):
            seen.add(path)
            candidates.append(path)

    which_ts = shutil.which("TotalSegmentator")
    if which_ts:
        _add(which_ts)

    script_dirs: list[str] = []
    for scheme in (None, "nt_user", "posix_user"):
        try:
            path = (
                sysconfig.get_path("scripts")
                if scheme is None
                else sysconfig.get_path("scripts", scheme)
            )
        except (KeyError, TypeError, ValueError):
            path = None
        if path:
            script_dirs.append(path)

    # pip --user on Windows often lands here even when nt_user is odd.
    try:
        import site

        user_base = getattr(site, "USER_BASE", None) or ""
        if user_base:
            script_dirs.append(os.path.join(user_base, "Scripts"))
            script_dirs.append(os.path.join(user_base, "bin"))
    except ImportError:
        pass

    for scripts_dir in script_dirs:
        for name in names:
            _add(os.path.join(scripts_dir, name))
    return candidates


def _total_segmentator_launch_command() -> list[str]:
    """Prefer TotalSegmentator console script; else ``-m totalsegmentator.bin.TotalSegmentator``.

    Note: ``python -m totalsegmentator`` fails — the package has no ``__main__``.
    """
    python_slicer = shutil.which("PythonSlicer")
    ts_scripts = _total_segmentator_script_candidates()

    if python_slicer and ts_scripts:
        return [python_slicer, ts_scripts[0]]
    if ts_scripts:
        # Console script shebang/wrapper already targets the install's Python.
        return [ts_scripts[0]]

    # Module path used by setuptools entry point TotalSegmentator=...:main
    module = "totalsegmentator.bin.TotalSegmentator"
    if python_slicer:
        return [python_slicer, "-m", module]
    return [sys.executable, "-m", module]


def _total_segmentator_cli_options(
    input_path: Path,
    output_path: Path,
    device: str,
    hub_event: str,
    task: str = TS_TASK,
    fast: bool = TS_FAST,
) -> list[str]:
    del hub_event  # inbound event only stages input; output is always DICOM SEG
    options = [
        "-i",
        str(input_path.resolve()),
        "-o",
        str(output_path.resolve()),
        "--task",
        task or TS_TASK,
        "-ot",
        "dicom_seg",
        "-d",
        device,
        "-nr",
        "1",
        "-ns",
        "1",
    ]
    if fast:
        options.append("--fast")
    return options


class TotalSegFastUnsupportedError(RuntimeError):
    """TotalSegmentator exited because ``--fast`` is not allowed for the task."""


class TotalSegEmptySegmentsError(RuntimeError):
    """TotalSegmentator finished but DICOM SEG had no non-empty segments to save."""


def _status_line_is_traceback_noise(text: str) -> bool:
    """Skip Python traceback framing in user-facing job status (keep console print)."""
    s = text.strip()
    if not s:
        return True
    if s == "Traceback (most recent call last):":
        return True
    if s.startswith('File "') or s.startswith("File '"):
        return True
    if s.startswith("~") or set(s) <= {"^", "~", " "}:
        return True
    if s.startswith("...") and "lines" in s:
        return True
    # Indented traceback continuation / caret lines
    if text.startswith("    ") or text.startswith("  File "):
        return True
    # Raw empty-SEG exception — replaced by a clearer _status_error below
    if "No non-empty segments found to save" in s:
        return True
    if s.startswith("ValueError:") or s.startswith("CalledProcessError:"):
        return True
    return False


def _log_subprocess_output(proc: Any) -> str:
    chunks: list[str] = []
    while True:
        try:
            line = proc.stdout.readline()
        except UnicodeDecodeError:
            continue
        if not line:
            break
        text = line.rstrip() if isinstance(line, str) else line.decode(
            "utf-8", errors="replace"
        ).rstrip()
        if text:
            chunks.append(text)
            print(text)
            if not _status_line_is_traceback_noise(text):
                _status_log_line(text)
    return "\n".join(chunks)


def _totalsegmentator_subprocess_env() -> Dict[str, str]:
    """Env for TotalSegmentator CLI — quiet noisy third-party FutureWarnings in job logs."""
    env = os.environ.copy()
    quiet = "ignore::FutureWarning"
    existing = (env.get("PYTHONWARNINGS") or "").strip()
    env["PYTHONWARNINGS"] = f"{existing},{quiet}" if existing else quiet
    return env


def _run_totalsegmentator_subprocess(
    command: list[str], options: list[str]
) -> bool:
    from subprocess import CalledProcessError

    cmd = command + options
    _debug_log("launch: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_totalsegmentator_subprocess_env(),
    )
    output = _log_subprocess_output(proc)
    proc.wait()
    if proc.returncode != 0:
        if "does not work with option --fast" in output:
            raise TotalSegFastUnsupportedError(
                "task does not work with option --fast"
            )
        if "No non-empty segments found to save" in output:
            raise TotalSegEmptySegmentsError(
                "No non-empty segments found to save"
            )
        raise CalledProcessError(proc.returncode, cmd)
    return True


def _run_totalsegmentator(
    input_path: Path,
    output_dir: Path,
    hub_event: str,
    task: str = TS_TASK,
    fast: bool = TS_FAST,
) -> Optional[Path]:
    command = _total_segmentator_launch_command()
    _debug_log("TotalSegmentator launch: %s", " ".join(command))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = _cli_output_path(output_dir, hub_event)
    use_fast = bool(fast)

    for device in ("gpu", "cpu"):
        try:
            while True:
                started = time.monotonic()
                options = _total_segmentator_cli_options(
                    input_path,
                    output_path,
                    device,
                    hub_event,
                    task=task,
                    fast=use_fast,
                )
                try:
                    _run_totalsegmentator_subprocess(command, options)
                except TotalSegFastUnsupportedError:
                    if not use_fast:
                        raise
                    _status_log(
                        "Task %s does not support --fast; retrying without fast…",
                        task,
                    )
                    _debug_log(
                        "retry without --fast task=%s device=%s", task, device
                    )
                    use_fast = False
                    if output_dir.is_dir():
                        for entry in output_dir.iterdir():
                            if entry.is_file():
                                entry.unlink()
                            elif entry.is_dir():
                                shutil.rmtree(entry, ignore_errors=True)
                    continue
                break

            elapsed = time.monotonic() - started
            _status_log(
                "Segmentation finished (%s, %.0fs)", device, elapsed
            )
            _debug_log(
                "finished device=%s in %.1fs hub.event=%s output=%s fast=%s",
                device,
                elapsed,
                hub_event,
                output_path,
                use_fast,
            )
            result = _find_segmentation_output(output_dir, hub_event, output_path)
            if result:
                return result
            _status_error("Segmentation failed: process exited without output")
            _debug_error(
                "exited OK but no output for hub.event=%s under %s",
                hub_event,
                output_dir,
            )
        except TotalSegEmptySegmentsError:
            _status_error(
                "No segmentation to save for task=%s — the model found no "
                "non-empty structures on this series (try task=total, or a "
                "different study/FOV).",
                task,
            )
            _debug_error(
                "empty DICOM SEG task=%s device=%s (job dir: %s)",
                task,
                device,
                output_dir,
            )
            return None
        except FileNotFoundError as exc:
            _status_error(
                "Segmentation failed: TotalSegmentator not found "
                "(install package or put PythonSlicer on PATH): %s",
                exc,
            )
            _debug_error("launch not found: %s", exc)
            return None
        except Exception as exc:
            _status_error("Segmentation failed (%s): %s", device, exc)
            _debug_error("failed device=%s: %s", device, exc)
            if output_dir.is_dir():
                for entry in output_dir.iterdir():
                    if entry.is_file():
                        entry.unlink()
                    elif entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
    return None

def _find_segmentation_output(
    output_dir: Path, hub_event: str, expected_path: Path
) -> Optional[Path]:
    del hub_event
    if expected_path.is_file():
        return expected_path
    return _find_output_dicom(output_dir)


def _find_output_dicom(output_dir: Path) -> Optional[Path]:
    primary = output_dir / OUTPUT_DICOM_NAME
    if primary.is_file():
        return primary
    candidates = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in (".dcm", ".dicom")
        and "input" not in path.parts
    )
    if not candidates:
        _debug_error(
            "no DICOM SEG under %s (contents: %s)",
            output_dir,
            list(output_dir.rglob("*"))[:20],
        )
        return None
    if len(candidates) > 1:
        _debug_log(
            "multiple DICOM outputs, using %s (all: %s)",
            candidates[0],
            [str(p) for p in candidates],
        )
    return candidates[0]


# ---------------------------------------------------------------------------
# Standalone CLI (service_provider.run_sync) — Slicer onMessage path unchanged
# ---------------------------------------------------------------------------


def _context_files(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    event = message.get("event") or {}
    ctx = event.get("context")
    if isinstance(ctx, dict):
        files = ctx.get("files")
        if isinstance(files, list):
            return [entry for entry in files if isinstance(entry, dict)]
    return []


def _ensure_job_status_context(
    topic: str, product_name: str, target_subscriber: str
) -> None:
    """Start a job once; reuse if ``on_send_download_start`` already started it."""
    ctx = _job_status_context
    if (
        ctx.get("topic") == topic
        and ctx.get("product_name") == product_name
        and ctx.get("job_number")
    ):
        _job_status_context["target_subscriber"] = (target_subscriber or "").strip()
        return
    _start_job(topic, product_name, target_subscriber)


def on_send_download_start(
    ctx: Any, message: Dict[str, Any], hub_event: str
) -> None:
    """Early status-update before the framework finishes downloading payloads."""
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()
    target_subscriber = str(message.get("subscriber.name") or "").strip()
    product_name = getattr(ctx, "product_name", "") or DEFAULT_PRODUCT_NAME
    if not topic:
        return
    _ensure_job_status_context(topic, product_name, target_subscriber)
    files = _context_files(message)
    _status_log(_format_download_started(files if files else []))


def _handle_standalone_send(
    ctx: Any,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
    hub_event: str,
) -> None:
    global _job_busy
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()
    if not topic:
        _debug_error("standalone %s missing hub.topic", hub_event)
        return

    product_name = getattr(ctx, "product_name", "") or DEFAULT_PRODUCT_NAME
    target_subscriber = str(message.get("subscriber.name") or "").strip()
    _ensure_job_status_context(topic, product_name, target_subscriber)
    _job_busy = True
    try:
        if file_count < 1:
            _status_error("Download failed: no %s payload received", hub_event)
            return
        files = _context_files(message)
        _status_log(_format_download_complete(files, file_count, total_bytes))
        job_dir = input_dir.parent
        job_output = job_dir / "output"
        job_output.mkdir(parents=True, exist_ok=True)
        _run_segmentation_job_body(
            topic,
            product_name,
            input_dir,
            job_output,
            job_dir,
            hub_event,
            message.get("event") if isinstance(message.get("event"), dict) else {},
        )
    finally:
        _job_busy = False
        _status_job_finished()
        _clear_job_status_context()


def on_dicom_send(
    ctx: Any,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    _handle_standalone_send(
        ctx, message, input_dir, file_count, total_bytes, _DICOM_SEND_EVENT
    )


def on_nifti_send(
    ctx: Any,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    _handle_standalone_send(
        ctx, message, input_dir, file_count, total_bytes, _NIFTI_SEND_EVENT
    )


if __name__ == "__main__":
    from service_provider import ServiceProviderConfig, ServiceProviderHandlers, run_sync

    HANDLERS = ServiceProviderHandlers(
        on_dicom_send=on_dicom_send,
        on_nifti_send=on_nifti_send,
        on_send_download_start=on_send_download_start,
        build_status_response=build_status_response,
    )
    run_sync(
        ServiceProviderConfig(product_name=DEFAULT_PRODUCT_NAME),
        HANDLERS,
    )
