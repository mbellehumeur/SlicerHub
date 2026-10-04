"""Hub-side idc-index helpers for resolving IDC series to per-file HTTPS URLs."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from importlib.metadata import distribution
from typing import Dict, List
from urllib.parse import quote

logger = logging.getLogger("hub.idc_index")

ACTION_ADD_STUDY = "addStudy"
SOURCE_BUCKET_DEFAULT = "aws"
MAX_SLICES_DEFAULT = 300

IDC_DICOMWEB_ROOT = (
    "https://proxy.imaging.datacommons.cancer.gov/current/"
    "viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
)
OPEN_MODE_DICOMWEB = "dicomweb"

AWS_ENDPOINT_URL = "https://s3.amazonaws.com"
GCP_ENDPOINT_URL = "https://storage.googleapis.com"
GCP_BUCKET_REPLACEMENTS = {
    r"s3://idc-open-data-two/": r"s3://idc-open-idc1/",
    r"s3://idc-open-data-cr/": r"s3://idc-open-cr/",
}

_idc_lock = threading.Lock()
_s5cmd_path: str | None = None
_series_urls_cache: Dict[str, List[str]] = {}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def s3_uri_to_public_https(url: str) -> str:
    if not url.startswith("s3://"):
        return url
    without_scheme = url[5:]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        return url
    encoded_key = "/".join(quote(segment, safe="") for segment in key.split("/"))
    return f"https://{bucket}.s3.amazonaws.com/{encoded_key}"


def format_size_mb(value: float) -> str:
    if value < 1:
        return f"{value:.1f} MB"
    return f"{int(round(value))} MB"


def _resolve_s5cmd_path() -> str:
    global _s5cmd_path
    if _s5cmd_path:
        return _s5cmd_path
    path = shutil.which("s5cmd")
    if path is None:
        try:
            for script in distribution("s5cmd").files:
                if str(script).startswith("s5cmd/bin/s5cmd"):
                    path = str(script.locate().resolve(strict=True))
                    break
        except Exception as exc:
            raise RuntimeError(
                "s5cmd executable not found. Install idc-index: pip install idc-index"
            ) from exc
    if not path:
        raise RuntimeError(
            "s5cmd executable not found. Install idc-index: pip install idc-index"
        )
    _s5cmd_path = path
    return path


def _idc_index_parquet_path() -> str:
    try:
        import idc_index_data
    except ImportError as exc:
        raise RuntimeError(
            "idc-index is not installed. Run: pip install idc-index"
        ) from exc
    path = idc_index_data.IDC_INDEX_PARQUET_FILEPATH
    if not path:
        raise RuntimeError("IDC index parquet path is not available")
    return str(path)


def _apply_gcp_bucket_replacements(s3_url: str) -> str:
    for old, new in GCP_BUCKET_REPLACEMENTS.items():
        s3_url = re.sub(old, new, s3_url)
    return s3_url


def _lookup_series_s3_prefix(series_uid: str, source_bucket: str) -> tuple[str, str]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "idc-index is not installed. Run: pip install idc-index"
        ) from exc

    parquet_path = _idc_index_parquet_path()
    row = duckdb.connect().execute(
        """
        SELECT aws_bucket, series_aws_url
        FROM read_parquet(?)
        WHERE SeriesInstanceUID = ?
        LIMIT 1
        """,
        [parquet_path, series_uid],
    ).fetchone()
    if not row:
        raise ValueError("SeriesInstanceUID not found in IDC index.")

    aws_bucket, series_aws_url = row
    crdc_series_uuid = str(series_aws_url).split("/")[3]
    s3_url = f"s3://{aws_bucket}/{crdc_series_uuid}/"

    if source_bucket == "gcs":
        s3_url = _apply_gcp_bucket_replacements(s3_url)
        endpoint = GCP_ENDPOINT_URL
    elif source_bucket == "aws":
        endpoint = AWS_ENDPOINT_URL
    else:
        raise ValueError(
            "Argument 'source_bucket_location' must be either 'gcs' or 'aws'."
        )
    return s3_url, endpoint


def _list_series_dicom_urls(s3_url: str, endpoint: str) -> List[str]:
    s5cmd_path = _resolve_s5cmd_path()
    result = subprocess.run(
        [
            s5cmd_path,
            "--endpoint-url",
            endpoint,
            "--no-sign-request",
            "ls",
            s3_url,
        ],
        stdout=subprocess.PIPE,
        check=False,
    )
    output = result.stdout.decode("utf-8")
    lines = output.split("\n")
    return [
        s3_url + line.split()[-1]
        for line in lines
        if line and line.split()[-1].endswith(".dcm")
    ]


def _series_urls_cache_key(series_uid: str, source_bucket: str) -> str:
    return f"{source_bucket}:{series_uid}"


def _get_series_file_urls(series_uid: str, source_bucket: str) -> List[str]:
    key = _series_urls_cache_key(series_uid, source_bucket)
    cached = _series_urls_cache.get(key)
    if cached is not None:
        logger.debug(
            "series URLs cache hit series=%s bucket=%s files=%d",
            series_uid,
            source_bucket,
            len(cached),
        )
        return cached

    t0 = time.time()
    with _idc_lock:
        cached = _series_urls_cache.get(key)
        if cached is not None:
            return cached
        s3_url, endpoint = _lookup_series_s3_prefix(series_uid, source_bucket)
        url_list = _list_series_dicom_urls(s3_url, endpoint)
        _series_urls_cache[key] = url_list

    logger.info(
        "get_series_file_URLs elapsed=%.1fs series=%s bucket=%s files=%d",
        time.time() - t0,
        series_uid,
        source_bucket,
        len(url_list),
    )
    return url_list


def _attach_series_urls(study: Dict[str, object], source_bucket: str) -> dict:
    series_uid = str(study.get("seriesInstanceUID") or "").strip()
    if not series_uid:
        raise ValueError("Missing seriesInstanceUID")

    slice_count = int(study.get("instanceCount") or 0)
    size_mb_raw = study.get("size")
    size_mb = 0.0
    if isinstance(size_mb_raw, (int, float)):
        size_mb = float(size_mb_raw)
    elif isinstance(size_mb_raw, str) and size_mb_raw.endswith(" MB"):
        try:
            size_mb = float(size_mb_raw.replace(" MB", "").strip())
        except ValueError:
            size_mb = 0.0

    urls = _get_series_file_urls(series_uid, source_bucket)
    if slice_count and len(urls) != slice_count:
        logger.warning(
            "URL count %d != instanceCount %d for series=%s",
            len(urls),
            slice_count,
            series_uid,
        )
    max_slices = _env_int("HUB_IDC_MAX_SLICES", MAX_SLICES_DEFAULT)
    if len(urls) > max_slices:
        raise ValueError(f"Series {series_uid} has {len(urls)} files (max {max_slices})")

    files = [
        {
            "url": s3_uri_to_public_https(url),
            "fileName": url.rsplit("/", 1)[-1],
        }
        for url in urls
    ]
    return {
        **study,
        "instanceCount": slice_count or len(urls),
        "size": format_size_mb(size_mb) if size_mb else f"{len(urls)} DICOM",
        "files": files,
    }


def resolve_study_series_files(
    study_in: Dict[str, object],
    organization: str = "",
    source_bucket: str = SOURCE_BUCKET_DEFAULT,
) -> Dict[str, object]:
    """Expand one worklist study dict with per-instance HTTPS files via idc-index."""
    if not isinstance(study_in, dict):
        raise ValueError("Missing study in request body")

    series_uid = str(study_in.get("seriesInstanceUID") or "").strip()
    if not series_uid:
        raise ValueError("Missing seriesInstanceUID in study")

    org = str(organization or study_in.get("organization") or "").strip()
    open_mode = str(study_in.get("openMode") or "").strip().lower()
    if not open_mode:
        open_mode = OPEN_MODE_DICOMWEB

    if open_mode == OPEN_MODE_DICOMWEB:
        study = dict(study_in)
        if not str(study.get("dicomwebRoot") or "").strip():
            study["dicomwebRoot"] = IDC_DICOMWEB_ROOT
        study["openMode"] = OPEN_MODE_DICOMWEB
        if org:
            study["organization"] = org
        bucket = (
            str(study_in.get("sourceBucket") or source_bucket).strip()
            or source_bucket
        )
        try:
            study = _attach_series_urls(study, bucket)
        except ValueError as exc:
            logger.warning(
                "dicomweb resolve: direct files skipped for series=%s: %s",
                series_uid,
                exc,
            )
        return {
            "source": "hub-idc-index",
            "action": ACTION_ADD_STUDY,
            "organization": org,
            "study": study,
        }

    bucket = str(study_in.get("sourceBucket") or source_bucket).strip() or source_bucket
    study = _attach_series_urls(dict(study_in), bucket)
    if org:
        study["organization"] = org
    return {
        "source": "hub-idc-index",
        "action": ACTION_ADD_STUDY,
        "organization": org,
        "study": study,
    }
