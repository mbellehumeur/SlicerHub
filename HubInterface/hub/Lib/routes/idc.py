"""IDC series/NL/translate/proxy-download and MCP proxy routes."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from idc_index_service import resolve_study_series_files
from idc_nl_anthropic import (
    anthropic_configured,
    env_idc_mcp_upstream_url,
    search_idc_via_anthropic,
)
from segment_label_translate import (
    segment_label_translate_configured,
    translate_segment_labels,
)

router = APIRouter()


# Browser clients cannot fetch GitHub release assets (no CORS). IRA and other
# Image Displays use this allowlisted proxy for remote NIfTI/NRRD opens.
_PROXY_DOWNLOAD_ALLOWED_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


def _proxy_download_host_allowed(hostname: str) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host in _PROXY_DOWNLOAD_ALLOWED_HOSTS:
        return True
    # Allow githubusercontent CDN subdomains used by release redirects.
    return host.endswith(".githubusercontent.com")


@router.get("/api/hub/proxy-download")
@router.get("/api/hub/proxy-download/")
async def proxy_download(request: Request, url: str = ""):
    """Stream an allowlisted remote file for browser Image Displays (CORS bridge)."""
    target = str(url or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Missing url query parameter")
    try:
        parsed = urlparse(target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid url: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    if not _proxy_download_host_allowed(parsed.hostname or ""):
        raise HTTPException(
            status_code=403,
            detail=f"Host not allowlisted for proxy-download: {parsed.hostname}",
        )

    timeout = aiohttp.ClientTimeout(total=600)
    try:
        session = aiohttp.ClientSession(timeout=timeout)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to create HTTP client: {exc}"
        ) from exc

    try:
        upstream = await session.get(target, allow_redirects=True)
    except Exception as exc:
        await session.close()
        raise HTTPException(
            status_code=502, detail=f"Upstream fetch failed: {exc}"
        ) from exc

    final_host = urlparse(str(upstream.url)).hostname or ""
    if not _proxy_download_host_allowed(final_host):
        upstream.close()
        await session.close()
        raise HTTPException(
            status_code=403,
            detail=f"Redirect host not allowlisted: {final_host}",
        )

    if upstream.status >= 400:
        body = await upstream.text()
        upstream.close()
        await session.close()
        raise HTTPException(
            status_code=502,
            detail=f"Upstream HTTP {upstream.status}: {body[:200]}",
        )

    media_type = (
        upstream.headers.get("Content-Type")
        or "application/octet-stream"
    )
    headers = {}
    content_length = upstream.headers.get("Content-Length")
    if content_length:
        headers["Content-Length"] = content_length
    content_disp = upstream.headers.get("Content-Disposition")
    if content_disp:
        headers["Content-Disposition"] = content_disp

    async def _stream():
        try:
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            upstream.close()
            await session.close()

    return StreamingResponse(_stream(), media_type=media_type, headers=headers)


IDC_MCP_UPSTREAM_DEFAULT = (
    "https://api.imaging.datacommons.cancer.gov/mcp"
)


def _env_idc_mcp_upstream_url() -> str:
    raw = os.getenv("HUB_IDC_MCP_UPSTREAM_URL", IDC_MCP_UPSTREAM_DEFAULT)
    return str(raw or IDC_MCP_UPSTREAM_DEFAULT).strip().rstrip("/")


def _env_idc_mcp_proxy_timeout_seconds() -> float:
    raw = os.getenv("HUB_IDC_MCP_PROXY_TIMEOUT_SECONDS", "180")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 180.0


@router.post("/api/hub/idc/series-files")
@router.post("/api/hub/idc/series-files/")
async def post_hub_idc_series_files(request: Request):
    """Resolve IDC series metadata to per-instance HTTPS files via idc-index."""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON body: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    study = payload.get("study")
    organization = str(payload.get("organization") or "").strip()
    source_bucket = str(payload.get("sourceBucket") or "aws").strip() or "aws"
    if not isinstance(study, dict):
        raise HTTPException(status_code=400, detail="Missing study object")

    started = time.time()
    try:
        result = await asyncio.to_thread(
            resolve_study_series_files,
            study,
            organization,
            source_bucket,
        )
    except MemoryError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "IDC index lookup exceeded available memory on this hub instance. "
                "Use a larger App Service plan or resolve series files locally."
            ),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status = 422 if "max" in message.lower() and "files" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger = logging.getLogger("hub")
        logger.exception(
            "idc series-files failed series=%s",
            str(study.get("seriesInstanceUID") or "").strip(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"IDC index lookup failed: {exc}",
        ) from exc

    resolved_study = result.get("study") if isinstance(result, dict) else {}
    file_count = len(resolved_study.get("files") or []) if isinstance(resolved_study, dict) else 0
    series_uid = str(study.get("seriesInstanceUID") or "").strip()
    logging.getLogger("hub").info(
        "idc series-files series=%s files=%d elapsed=%.0fms",
        series_uid,
        file_count,
        (time.time() - started) * 1000,
    )
    return result


@router.get("/api/hub/idc-nl/status")
@router.get("/api/hub/idc-nl/status/")
@router.get("/api/hub/idc-mcp/status")
@router.get("/api/hub/idc-mcp/status/")
async def get_hub_idc_nl_status():
    """Report Anthropic NL → SearchQuery availability for worklist clients."""
    return {
        "anthropic": anthropic_configured(),
        # Legacy field for older clients / MCP proxy URL display.
        "idcMcpUrl": env_idc_mcp_upstream_url(),
    }


@router.get("/api/hub/translate/status")
@router.get("/api/hub/translate/status/")
async def get_hub_translate_status():
    """Report Claude segment-label translate availability (lookup-table miss fallback)."""
    return {
        "segmentLabelTranslate": segment_label_translate_configured(),
        "provider": "anthropic",
    }


@router.post("/api/hub/translate")
@router.post("/api/hub/translate/")
async def post_hub_translate(request: Request):
    """Translate English segment labels via Anthropic when static locale maps miss."""
    if not segment_label_translate_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not configured on the Slicer hub. "
                "Set the environment variable (or hub/.env) and restart the hub."
            ),
        )
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON body: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    target = str(payload.get("target") or "").strip()
    source = str(payload.get("source") or "en").strip() or "en"
    raw_texts = payload.get("texts")
    if isinstance(raw_texts, str):
        texts = [raw_texts]
    elif isinstance(raw_texts, list):
        texts = [str(t) if t is not None else "" for t in raw_texts]
    else:
        raise HTTPException(
            status_code=400, detail="Missing texts (string or string array)"
        )
    if not target:
        raise HTTPException(status_code=400, detail="Missing target language")
    if len(texts) > 64:
        raise HTTPException(status_code=400, detail="At most 64 texts per request")

    try:
        translations = await asyncio.to_thread(
            translate_segment_labels, texts, target, source
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logging.getLogger("hub").exception("segment label translate failed")
        raise HTTPException(
            status_code=502, detail=f"Segment label translate failed: {exc}"
        ) from exc

    return {
        "translations": translations,
        "target": target,
        "source": source,
        "provider": "anthropic",
    }


@router.post("/api/hub/idc-nl/search")
@router.post("/api/hub/idc-nl/search/")
@router.post("/api/hub/idc-mcp/search")
@router.post("/api/hub/idc-mcp/search/")
async def post_hub_idc_nl_search(request: Request):
    """NL → SearchQuery JSON via Anthropic (client executes against IDC REST)."""
    if not anthropic_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not configured on the Slicer hub. "
                "Set the environment variable and restart the hub."
            ),
        )
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON body: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing prompt")

    max_rows_raw = payload.get("max_rows", 20)
    try:
        max_rows = int(max_rows_raw)
    except (TypeError, ValueError):
        max_rows = 20

    started = time.time()
    try:
        result = await asyncio.to_thread(
            search_idc_via_anthropic, prompt, max_rows
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger = logging.getLogger("hub")
        logger.exception("idc-nl anthropic search failed")
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic IDC search failed: {exc}",
        ) from exc

    query = result.get("query") if isinstance(result, dict) else {}
    query_type = ""
    if isinstance(query, dict):
        query_type = str(query.get("queryType") or "")
    logging.getLogger("hub").info(
        "idc-nl anthropic nl-query prompt_len=%d queryType=%s elapsed=%.0fms",
        len(prompt),
        query_type or "?",
        (time.time() - started) * 1000,
    )
    return result


@router.post("/idc-mcp-proxy/mcp")
async def idc_mcp_proxy(request: Request):
    """Streamable HTTP proxy to the official IDC MCP endpoint."""
    upstream = _env_idc_mcp_upstream_url()
    body = await request.body()
    forward_headers: Dict[str, str] = {}
    for header_name in ("content-type", "accept", "mcp-session-id"):
        value = request.headers.get(header_name)
        if value:
            forward_headers[header_name] = value

    timeout = aiohttp.ClientTimeout(total=_env_idc_mcp_proxy_timeout_seconds())
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                upstream,
                data=body,
                headers=forward_headers,
            ) as upstream_resp:
                response_headers: Dict[str, str] = {}
                for header_name in ("content-type", "mcp-session-id"):
                    value = upstream_resp.headers.get(header_name)
                    if value:
                        response_headers[header_name] = value
                content = await upstream_resp.read()
                return Response(
                    content=content,
                    status_code=upstream_resp.status,
                    headers=response_headers,
                )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="IDC MCP upstream request timed out",
        ) from exc
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"IDC MCP upstream error: {exc}",
        ) from exc
