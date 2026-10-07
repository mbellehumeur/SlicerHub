#!/usr/bin/env python3
"""DENTAL_SEG Hub service provider — standalone CLI entry point.

Run from repo root (plain Python, no 3D Slicer):

    pip install aiohttp nnunetv2
    python service_providers/products/dental_segmentator.py
    python service_providers/products/dental_segmentator.py --local

Default hub is SLICER-HUB-CLOUD; ``--local`` uses ``http://127.0.0.1:2018``.

On inbound nifti-send: download, run nnU-Net v2 DentalSegmentator
(``nnUNetv2_predict_from_modelfolder``), publish labelmap via nifti-send.

On inbound dicom-send: status error (convert to NIfTI first / use nifti-send).

Model weights (Dataset111): if ``DENTAL_SEG_MODEL_PATH`` is unset, the script
downloads and caches ``Dataset111_453CT_v100.zip`` under
``~/.slicer-hub/models/dental_segmentator`` on first job. Override with the
env var to use a pre-unzipped weights folder (``dataset.json`` / ``fold_0``).

Upstream: https://github.com/gaudot/SlicerDentalSegmentator

See ``service_provider.py`` and ``dental_segmentator-readme.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

LOGGER = logging.getLogger("DENTAL_SEG")

PRODUCT_NAME = "DENTAL_SEG"
FINAL_STATUS_LINE = "Segmentation complete"
CASE_ID = "dental"
MODEL_PATH_ENV = "DENTAL_SEG_MODEL_PATH"
DEVICE_ENV = "DENTAL_SEG_DEVICE"
WEIGHTS_ZIP_URL = (
    "https://github.com/gaudot/SlicerDentalSegmentator/releases/download/"
    "v1.0.0-alpha/Dataset111_453CT_v100.zip"
)
WEIGHTS_ZIP_NAME = "Dataset111_453CT_v100.zip"
_MODEL_ENSURE_LOCK = threading.Lock()


def _default_model_cache_dir() -> Path:
    return Path.home() / ".slicer-hub" / "models" / "dental_segmentator"


def _cache_looks_like_model(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        _resolve_nnunet_model_folder(root)
        return True
    except FileNotFoundError:
        return False


def _download_file(url: str, dest: Path, on_progress: Optional[Callable[[str], None]] = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    if partial.is_file():
        partial.unlink()

    def _report(msg: str) -> None:
        LOGGER.info("DENTAL_SEG: %s", msg)
        if on_progress:
            on_progress(msg)

    _report(f"Downloading DentalSegmentator weights (~220 MB) from {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_pct = -1
            with open(partial, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total > 0:
                        pct = int(100 * done / total)
                        if pct >= last_pct + 10 or pct == 100:
                            last_pct = pct
                            mb = done / (1024 * 1024)
                            total_mb = total / (1024 * 1024)
                            _report(
                                f"Downloading weights… {pct}% "
                                f"({mb:.0f}/{total_mb:.0f} MB)"
                            )
    except urllib.error.URLError as exc:
        if partial.is_file():
            partial.unlink()
        raise FileNotFoundError(
            f"Failed to download DentalSegmentator weights from {url}: {exc}"
        ) from exc

    partial.replace(dest)
    _report(f"Downloaded {dest.name}")


def _ensure_cached_model(
    on_progress: Optional[Callable[[str], None]] = None,
) -> Path:
    """Download+unzip Dataset111 into the default cache if needed."""
    cache = _default_model_cache_dir()
    with _MODEL_ENSURE_LOCK:
        if _cache_looks_like_model(cache):
            return cache.resolve()

        def _report(msg: str) -> None:
            LOGGER.info("DENTAL_SEG: %s", msg)
            if on_progress:
                on_progress(msg)

        cache.mkdir(parents=True, exist_ok=True)
        zip_path = cache / WEIGHTS_ZIP_NAME
        if not zip_path.is_file():
            _download_file(WEIGHTS_ZIP_URL, zip_path, on_progress=on_progress)
        else:
            _report(f"Using cached zip {zip_path}")

        _report("Unzipping DentalSegmentator weights…")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(cache)
        except zipfile.BadZipFile as exc:
            zip_path.unlink(missing_ok=True)
            raise FileNotFoundError(
                f"Corrupt weights zip {zip_path}; deleted — re-run to download again"
            ) from exc

        if not _cache_looks_like_model(cache):
            raise FileNotFoundError(
                f"Unzipped weights under {cache} but no nnUNet model "
                "(fold_0 / dataset.json) was found"
            )
        _report(f"DentalSegmentator weights ready at {cache}")
        return cache.resolve()


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
            f"Downloading dental CT/CBCT, {file_count} files, "
            f"total {mb_text} MB."
        )
    return f"Downloading dental CT/CBCT, {file_count} files."


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
        "DENTAL_SEG: status-update %r -> %s (HTTP %s)",
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
            "DENTAL_SEG: inbound send has no context.files[]; "
            "skipping download status-update"
        )
        return
    _publish_to_requester(
        ctx, message, _format_download_status_line(file_count, total_bytes)
    )


def _resolve_input_nifti(input_dir: Path) -> Optional[Path]:
    for pattern in ("*.nii.gz", "*.nii"):
        matches = sorted(input_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def _model_root(
    on_progress: Optional[Callable[[str], None]] = None,
) -> Path:
    raw = (os.environ.get(MODEL_PATH_ENV) or "").strip()
    if not raw:
        return _ensure_cached_model(on_progress=on_progress)
    # Git Bash turns /path/... into C:\Program Files\Git\path\...
    lowered = raw.replace("\\", "/").lower()
    if (
        "/path/to/" in lowered
        or "program files/git/path" in lowered
        or lowered.endswith("/unzipped/dataset111/weights")
    ):
        raise FileNotFoundError(
            f"{MODEL_PATH_ENV} looks like a documentation placeholder ({raw!r}). "
            "Unset it to auto-download into ~/.slicer-hub/models/dental_segmentator, "
            "or set it to a real unzipped Dataset111 folder "
            "(Git Bash: use C:/... not /path/to/...)."
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"{MODEL_PATH_ENV} is not a directory: {path}")
    return path


def _resolve_nnunet_model_folder(model_root: Path) -> Path:
    """Return the nnUNet trained-model folder suitable for ``-m``.

    Accepts either the trainer folder itself (has ``fold_0``) or a weights
    root that contains ``dataset.json`` / nested trainer dirs (Slicer ML layout).
    """
    if (model_root / "fold_0").is_dir() or (
        model_root / "dataset.json"
    ).is_file() and any(model_root.glob("fold_*")):
        return model_root

    fold_dirs = sorted(model_root.rglob("fold_0"))
    for fold in fold_dirs:
        parent = fold.parent
        if (parent / "plans.json").is_file() or (
            parent / "dataset.json"
        ).is_file():
            return parent
        return parent

    dataset_json = next(model_root.rglob("dataset.json"), None)
    if dataset_json is not None:
        # Prefer sibling trainer folder under the dataset dir.
        dataset_dir = dataset_json.parent
        trainers = sorted(dataset_dir.glob("nnUNetTrainer*"))
        if trainers:
            return trainers[0]
        return dataset_dir

    raise FileNotFoundError(
        f"No nnUNet model (fold_0 / dataset.json) under {model_root}"
    )


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _mps_available() -> bool:
    try:
        import torch

        backend = getattr(torch.backends, "mps", None)
        return bool(backend is not None and backend.is_available())
    except ImportError:
        return False


def _prediction_device() -> str:
    """Resolve inference device; fall back when CUDA/MPS is requested but missing."""
    raw = (os.environ.get(DEVICE_ENV) or "").strip().lower()
    requested = raw if raw in ("cuda", "cpu", "mps") else ""

    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if _cuda_available():
            return "cuda"
        LOGGER.warning(
            "DENTAL_SEG: %s=cuda but no GPU - using cpu",
            DEVICE_ENV,
        )
        return "cpu"
    if requested == "mps":
        if _mps_available():
            return "mps"
        LOGGER.warning(
            "DENTAL_SEG: %s=mps but unavailable - using cpu",
            DEVICE_ENV,
        )
        return "cpu"

    # Auto: prefer CUDA, then MPS, else CPU.
    if _cuda_available():
        return "cuda"
    if _mps_available():
        return "mps"
    LOGGER.info("DENTAL_SEG: no GPU detected - using cpu (slow)")
    return "cpu"


def _nnunet_launch_command(args: List[str]) -> List[str]:
    """Build argv for ``nnUNetv2_predict_from_modelfolder`` (console script or -c)."""
    which = shutil.which("nnUNetv2_predict_from_modelfolder")
    if which:
        return [which, *args]
    # Module ``__main__`` is the dataset-id entry point; call modelfolder entry.
    return [
        sys.executable,
        "-c",
        (
            "from nnunetv2.inference.predict_from_raw_data import "
            "predict_entry_point_modelfolder; "
            "predict_entry_point_modelfolder()"
        ),
        *args,
    ]


def _stage_nnunet_input(nifti_path: Path, stage_dir: Path) -> Path:
    """nnUNet expects ``{case}_0000.nii.gz`` in the input folder."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    for old in stage_dir.iterdir():
        if old.is_file():
            old.unlink()
    dest = stage_dir / f"{CASE_ID}_0000.nii.gz"
    shutil.copy2(nifti_path, dest)
    return dest


def _find_prediction_output(pred_dir: Path) -> Optional[Path]:
    preferred = pred_dir / f"{CASE_ID}.nii.gz"
    if preferred.is_file():
        return preferred
    matches = sorted(pred_dir.glob("*.nii.gz")) + sorted(pred_dir.glob("*.nii"))
    return matches[0] if matches else None


def _run_nnunet_dental(
    nifti_path: Path,
    job_dir: Path,
    on_line: Optional[Any] = None,
    on_model_progress: Optional[Callable[[str], None]] = None,
) -> Path:
    model_folder = _resolve_nnunet_model_folder(
        _model_root(on_progress=on_model_progress)
    )
    device = _prediction_device()
    work_in = job_dir / "nnunet_in"
    work_out = job_dir / "nnunet_out"
    work_out.mkdir(parents=True, exist_ok=True)
    staged = _stage_nnunet_input(nifti_path, work_in)
    LOGGER.info(
        "DENTAL_SEG: nnUNet model=%s device=%s staged=%s",
        model_folder,
        device,
        staged,
    )

    args = [
        "-i",
        str(work_in.resolve()),
        "-o",
        str(work_out.resolve()),
        "-m",
        str(model_folder.resolve()),
        "-f",
        "0",
        "-device",
        device,
        "-npp",
        "1",
        "-nps",
        "1",
        "--disable_tta",
        "--disable_progress_bar",
    ]
    if device == "cpu":
        # Avoid CUDA pin_memory paths in nnU-Net worker processes on CPU-only hosts.
        args.append("--not_on_device")
    cmd = _nnunet_launch_command(args)

    LOGGER.info("DENTAL_SEG: launch %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        text = line.rstrip()
        if text:
            print(text)
            LOGGER.info("DENTAL_SEG| %s", text)
            if on_line:
                on_line(text)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(
            f"nnUNetv2_predict_from_modelfolder exited {proc.returncode}"
        )

    result = _find_prediction_output(work_out)
    if result is None:
        raise FileNotFoundError(f"No segmentation NIfTI under {work_out}")
    return result


def _publish_result_nifti_sync(
    ctx: ServiceProviderContext, topic: str, result_path: Path
) -> None:
    future = asyncio.run_coroutine_threadsafe(
        ctx.publish_nifti_send(topic, str(result_path)),
        ctx.loop,
    )
    http_status = future.result(timeout=300.0)
    LOGGER.info("DENTAL_SEG: published result HTTP %s", http_status)


def _require_job_routing(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    input_dir: Path,
    label: str,
) -> Optional[str]:
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip()
    LOGGER.info(
        "DENTAL_SEG: received %s id=%s topic=%s",
        label,
        message.get("id", ""),
        topic,
    )
    ctx.write_directory_manifest(input_dir)
    if not topic:
        LOGGER.warning("DENTAL_SEG: inbound %s missing hub.topic", label)
        return None
    if not str(message.get("subscriber.name") or "").strip():
        LOGGER.warning(
            "DENTAL_SEG: inbound %s missing subscriber.name; "
            "cannot send status-update",
            label,
        )
        return None
    return topic


def on_dicom_send(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    del file_count, total_bytes
    topic = _require_job_routing(ctx, message, input_dir, "dicom-send")
    if topic is None:
        return
    _publish_to_requester(
        ctx,
        message,
        "ERROR: dicom-send not implemented yet — send a NIfTI via nifti-send "
        "(or convert DICOM→NIfTI first).",
    )


def on_nifti_send(
    ctx: ServiceProviderContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    del file_count, total_bytes
    topic = _require_job_routing(ctx, message, input_dir, "nifti-send")
    if topic is None:
        return

    _publish_to_requester(ctx, message, "Download complete.")
    nifti_path = _resolve_input_nifti(input_dir)
    if nifti_path is None:
        _publish_to_requester(ctx, message, "ERROR: no NIfTI file in input")
        return

    LOGGER.info("DENTAL_SEG: input NIfTI %s", nifti_path)
    job_dir = input_dir.parent

    def _status_line(text: str) -> None:
        # Keep requester log readable — only forward short progress-ish lines.
        if len(text) > 200:
            return
        if text.startswith("Predicting") or text.startswith("Processing"):
            _publish_to_requester(ctx, message, text[:180])

    try:
        device = _prediction_device()

        def _model_progress(text: str) -> None:
            _publish_to_requester(ctx, message, text[:180])

        _publish_to_requester(
            ctx,
            message,
            f"Running DentalSegmentator (nnU-Net, device={device})…",
        )
        result_path = _run_nnunet_dental(
            nifti_path,
            job_dir,
            on_line=_status_line,
            on_model_progress=_model_progress,
        )
    except FileNotFoundError as exc:
        _publish_to_requester(ctx, message, f"ERROR: {exc}")
        LOGGER.exception("DENTAL_SEG: model/input error")
        return
    except Exception as exc:
        _publish_to_requester(
            ctx, message, f"ERROR: segmentation failed: {exc}"
        )
        LOGGER.exception("DENTAL_SEG: inference failed")
        return

    _publish_to_requester(ctx, message, "Publishing result…")
    try:
        _publish_result_nifti_sync(ctx, topic, result_path)
    except Exception as exc:
        _publish_to_requester(
            ctx, message, f"ERROR: publish nifti-send failed: {exc}"
        )
        LOGGER.exception("DENTAL_SEG: publish failed")
        return

    _publish_to_requester(ctx, message, FINAL_STATUS_LINE)
    LOGGER.info("DENTAL_SEG: job complete result=%s", result_path)


HANDLERS = ServiceProviderHandlers(
    on_dicom_send=on_dicom_send,
    on_nifti_send=on_nifti_send,
    on_send_download_start=on_send_download_start,
    build_status_response=build_status_response,
)


if __name__ == "__main__":
    run_sync(ServiceProviderConfig(product_name=PRODUCT_NAME), HANDLERS)
