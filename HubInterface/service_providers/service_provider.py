#!/usr/bin/env python3
"""Reusable standalone Hub resource-server framework (no 3D Slicer UI).

Connects to SLICER-HUB-CLOUD by default (``--local`` uses ``http://127.0.0.1:2018``).
Dispatches dicom-send / nifti-send / status-request, downloads inbound files
(HTTP URL first, hub payloadIds fallback), and exposes publish helpers on
``ResourceServerContext``.

Product scripts (e.g. ``lung_screening.py``) supply handlers and call ``run_sync()``.

Dependencies (stdlib + aiohttp): ``pip install aiohttp`` or use
``hub/requirements.txt``. Install ``pip install -e python_client`` first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("HubResourceServer")

SLICER_HUB_CLOUD = {
    "name": "SLICER-HUB-CLOUD",
    "hub_endpoint": (
        "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/api/hub"
    ),
    "authorization_endpoint": (
        "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/authorize"
    ),
    "token_endpoint": (
        "https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/oauth/token"
    ),
    "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
    "client_secret": "0c931e4163c1bc984b5266735dc652a2f1e3e6e8d8cfe5b0855f433cc8ff018f",
    "lease": 999,
}

SLICER_HUB_LOCAL = {
    "name": "SLICER-HUB",
    "hub_endpoint": "http://127.0.0.1:2018/api/hub",
    "authorization_endpoint": "http://127.0.0.1:2018/oauth/authorize",
    "token_endpoint": "http://127.0.0.1:2018/oauth/token",
    "client_id": "130c3d9c-4157-4dd1-aa1d-slicer",
    "client_secret": "0c931e4163c1bc984b5266735dc652a2f1e3e6e8d8cfe5b0855f433cc8ff018f",
    "lease": 999,
}

_DICOM_SEND_EVENT = "dicom-send"
_NIFTI_SEND_EVENT = "nifti-send"


def format_connect_failure(exc: BaseException) -> str:
    """Short, user-facing reason for a failed hub connect (no aiohttp stack dump)."""
    if isinstance(exc, RuntimeError):
        return str(exc).strip() or exc.__class__.__name__

    text = str(exc).strip()
    lowered = text.lower()
    root: BaseException = exc
    cause = exc.__cause__ or exc.__context__
    while cause is not None and cause is not root:
        root = cause
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)

    root_name = root.__class__.__name__
    root_text = str(root).strip()
    root_lower = root_text.lower()

    if "refused" in lowered or "refused" in root_lower:
        return "connection refused (hub not reachable)"
    if (
        "timed out" in lowered
        or "timeout" in lowered
        or "timed out" in root_lower
        or root_name.endswith("TimeoutError")
    ):
        return "connection timed out"
    if (
        "name or service not known" in lowered
        or "getaddrinfo failed" in lowered
        or "nodename nor servname" in lowered
    ):
        return "DNS lookup failed"
    if "ssl" in lowered and ("certificate" in lowered or "handshake" in lowered):
        return f"TLS/SSL error: {root_text or text}"
    if "Cannot connect to host" in text:
        return text
    if root_text and root_text != text:
        return f"{root_name}: {root_text}"
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


def _ensure_hub_imports() -> None:
    """Add monorepo roots for python_client and runtime (no Slicer)."""
    repo_root = Path(__file__).resolve().parents[1]
    rs_root = Path(__file__).resolve().parent
    for extra in (repo_root / "python_client" / "src", rs_root, rs_root / "runtime"):
        extra_str = str(extra)
        if extra.is_dir() and extra_str not in sys.path:
            sys.path.insert(0, extra_str)


_ensure_hub_imports()

from hub_client import (  # noqa: E402
    HubClientOptions,
    HubConfig,
    SessionConfig,
    SlicerHubClient,
    data_type_from_event_name,
    hub_event_name,
    is_request_event,
    normalize_data_type,
    request_context,
)
from provider_runtime import (  # noqa: E402
    build_dicom_send_publish_message,
    build_nifti_send_publish_message,
    build_status_update_publish_message,
    extract_all_dicom_send_files_to_dir,
    extract_all_nifti_send_files_to_dir,
    register_connection,
    unregister_connection,
)


@dataclass
class ResourceServerConfig:
    product_name: str
    topic: str = "*"
    actors: List[str] = field(default_factory=lambda: ["EC"])
    events: List[str] = field(
        default_factory=lambda: ["dicom-send", "nifti-send", "status-request"]
    )
    user_name: str = "3dslicer-server"
    product_version: str = "1.0"
    auto_reconnect: bool = True
    use_local_hub: bool = False


def resolve_hub_preset(use_local_hub: bool = False) -> Dict[str, Any]:
    """Return hub OAuth/endpoints for cloud (default) or local Slicer hub."""
    return SLICER_HUB_LOCAL if use_local_hub else SLICER_HUB_CLOUD


def parse_resource_server_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Shared CLI flags for standalone product entry scripts."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Connect to the local Slicer hub at http://127.0.0.1:2018 (default: cloud hub).",
    )
    return parser.parse_args(argv)


@dataclass
class ResourceServerHandlers:
    on_dicom_send: Optional[
        Callable[["ResourceServerContext", Dict[str, Any], Path, int, int], None]
    ] = None
    on_nifti_send: Optional[
        Callable[["ResourceServerContext", Dict[str, Any], Path, int, int], None]
    ] = None
    on_send_download_start: Optional[
        Callable[["ResourceServerContext", Dict[str, Any], str], None]
    ] = None
    build_status_response: Optional[
        Callable[["ResourceServerContext"], Dict[str, Any]]
    ] = None


class _StandaloneHubConnection:
    """Minimal hub connection object for ``provider_runtime.register_connection``."""

    def __init__(self, client: SlicerHubClient, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    def schedule_publish(self, hub_message: Dict[str, Any]) -> None:
        if not self._client or not self._loop:
            LOGGER.warning("schedule_publish: hub not connected")
            return
        future = asyncio.run_coroutine_threadsafe(
            self._client.publish(hub_message), self._loop
        )

        def _log_result(done: "asyncio.Future[Any]") -> None:
            try:
                status = done.result()
                LOGGER.info("Hub publish finished HTTP %s", status)
            except Exception as exc:
                LOGGER.exception("Hub publish failed: %s", exc)

        future.add_done_callback(_log_result)


class ResourceServerContext:
    def __init__(
        self,
        client: SlicerHubClient,
        config: ResourceServerConfig,
        loop: asyncio.AbstractEventLoop,
        hub_connection: _StandaloneHubConnection,
    ) -> None:
        self.client = client
        self.config = config
        self.loop = loop
        self._hub_connection = hub_connection

    async def publish_status_update(
        self,
        topic: str,
        target_subscriber_name: str,
        message: str,
        level: str = "info",
    ) -> Optional[int]:
        hub_message = build_status_update_publish_message(
            topic, target_subscriber_name, message, level
        )
        return await self.client.publish(hub_message)

    def publish_status_update_sync(
        self,
        topic: str,
        target_subscriber_name: str,
        message: str,
        level: str = "info",
        timeout: float = 60.0,
    ) -> Optional[int]:
        future = asyncio.run_coroutine_threadsafe(
            self.publish_status_update(
                topic, target_subscriber_name, message, level
            ),
            self.loop,
        )
        return future.result(timeout=timeout)

    async def publish_dicom_send(self, topic: str, file_path: str) -> Optional[int]:
        hub_message = build_dicom_send_publish_message(topic, file_path)
        return await self.client.publish(hub_message)

    async def publish_nifti_send(self, topic: str, file_path: str) -> Optional[int]:
        hub_message = build_nifti_send_publish_message(topic, file_path)
        return await self.client.publish(hub_message)

    def write_directory_manifest(
        self, input_dir: Path, manifest_path: Optional[Path] = None
    ) -> Path:
        input_path = Path(input_dir)
        if manifest_path is None:
            manifest_path = input_path.parent / "downloaded-files.txt"

        lines: List[str] = []
        if input_path.is_dir():
            for path in sorted(input_path.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(input_path)
                    size = path.stat().st_size
                    line = f"{rel}\t{size}"
                    lines.append(line)
                    LOGGER.info(
                        "%s: file %s (%d bytes)",
                        self.config.product_name,
                        rel,
                        size,
                    )
        else:
            LOGGER.warning(
                "%s: input directory missing: %s",
                self.config.product_name,
                input_path,
            )

        manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        LOGGER.info(
            "%s: wrote directory manifest (%d files) to %s",
            self.config.product_name,
            len(lines),
            manifest_path,
        )
        return manifest_path


def _safe_topic_dir_name(topic: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", topic.strip())
    return safe or "topic"


def _allocate_job_input_dir(product_name: str, topic: str) -> Path:
    stamp = int(time.time() * 1000)
    job_dir = (
        Path(tempfile.gettempdir())
        / "hub-rs"
        / _safe_topic_dir_name(product_name)
        / f"{_safe_topic_dir_name(topic)}-{stamp}"
    )
    job_input = job_dir / "input"
    job_input.mkdir(parents=True, exist_ok=True)
    return job_input


def _download_send_files(
    message: Dict[str, Any],
    hub_event: str,
    product_name: str,
) -> Tuple[Path, int, int]:
    event = message.get("event") or {}
    topic = (event.get("hub.topic") or "").strip() or "topic"
    job_input = _allocate_job_input_dir(product_name, topic)

    if hub_event == _DICOM_SEND_EVENT:
        file_count, total_bytes = extract_all_dicom_send_files_to_dir(
            message, job_input, product_name
        )
    elif hub_event == _NIFTI_SEND_EVENT:
        file_count, total_bytes = extract_all_nifti_send_files_to_dir(
            message, job_input, product_name
        )
    else:
        raise ValueError(f"unsupported hub event for download: {hub_event}")

    return job_input, file_count, total_bytes


def _default_status_response(config: ResourceServerConfig) -> Dict[str, Any]:
    return {
        "source": "status",
        "product": config.product_name,
        "items": [{"key": "availability", "value": "online"}],
    }


def _build_client(config: ResourceServerConfig) -> SlicerHubClient:
    hub_def = resolve_hub_preset(config.use_local_hub)
    hub = HubConfig(
        hub_endpoint=hub_def["hub_endpoint"],
        authorization_endpoint=hub_def["authorization_endpoint"],
        token_endpoint=hub_def["token_endpoint"],
        client_id=hub_def["client_id"],
        client_secret=hub_def["client_secret"],
    )
    session = SessionConfig(
        topic=config.topic,
        product_name=config.product_name,
        product_version=config.product_version,
        actors=list(config.actors),
        events=list(config.events),
        lease=int(hub_def["lease"]),
        user_name=config.user_name,
        default_target_actor="",
    )
    options = HubClientOptions(auto_reconnect=config.auto_reconnect)
    return SlicerHubClient(hub, session, options)


async def _connect(client: SlicerHubClient, config: ResourceServerConfig) -> None:
    hub_def = resolve_hub_preset(config.use_local_hub)
    hub_name = hub_def["name"]
    auth = await client.authenticate()
    LOGGER.info(
        "%s: authenticated user_name=%s",
        config.product_name,
        auth.get("user_name"),
    )
    code = auth.get("code")
    if not code:
        raise RuntimeError("authenticate did not return a code")
    if not await client.get_token(code):
        raise RuntimeError("get_token failed")
    status = await client.subscribe()
    if status != 202:
        raise RuntimeError(f"subscribe failed with HTTP {status}")
    LOGGER.info(
        "%s: subscribed hub=%s topic=%s events=%s endpoint=%s",
        config.product_name,
        hub_name,
        config.topic,
        ",".join(config.events),
        hub_def["hub_endpoint"],
    )


def _invoke_handler(
    handler: Optional[Callable[..., None]],
    ctx: ResourceServerContext,
    message: Dict[str, Any],
    input_dir: Path,
    file_count: int,
    total_bytes: int,
) -> None:
    if handler is None:
        return
    handler(ctx, message, input_dir, file_count, total_bytes)


async def _handle_send_event(
    ctx: ResourceServerContext,
    handlers: ResourceServerHandlers,
    message: Dict[str, Any],
    hub_event: str,
) -> None:
    event = message.get("event") or {}
    LOGGER.info(
        "%s: received %s id=%s topic=%s",
        ctx.config.product_name,
        hub_event,
        message.get("id", ""),
        event.get("hub.topic", ""),
    )

    def _work() -> None:
        try:
            if handlers.on_send_download_start is not None:
                handlers.on_send_download_start(ctx, message, hub_event)
            input_dir, file_count, total_bytes = _download_send_files(
                message, hub_event, ctx.config.product_name
            )
            LOGGER.info(
                "%s: downloaded %s files=%d bytes=%d dir=%s",
                ctx.config.product_name,
                hub_event,
                file_count,
                total_bytes,
                input_dir,
            )
            if hub_event == _DICOM_SEND_EVENT:
                _invoke_handler(
                    handlers.on_dicom_send,
                    ctx,
                    message,
                    input_dir,
                    file_count,
                    total_bytes,
                )
            elif hub_event == _NIFTI_SEND_EVENT:
                _invoke_handler(
                    handlers.on_nifti_send,
                    ctx,
                    message,
                    input_dir,
                    file_count,
                    total_bytes,
                )
        except Exception:
            LOGGER.exception(
                "%s: handler failed for %s id=%s",
                ctx.config.product_name,
                hub_event,
                message.get("id", ""),
            )

    await asyncio.to_thread(_work)


async def _handle_status_request(
    ctx: ResourceServerContext,
    handlers: ResourceServerHandlers,
    message: Dict[str, Any],
) -> None:
    event_name = hub_event_name(message)
    if not is_request_event(event_name):
        return

    req_ctx = request_context(message)
    data_type = req_ctx.get("dataType") or data_type_from_event_name(event_name)
    if normalize_data_type(data_type) != normalize_data_type("STATUS"):
        return

    correlation_id = req_ctx.get("id")
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        return

    if handlers.build_status_response is not None:
        payload = handlers.build_status_response(ctx)
    else:
        payload = _default_status_response(ctx.config)

    event = message.get("event") or {}
    ctx.client.send_hub_request_response(
        correlation_id.strip(),
        "STATUS",
        payload,
        event.get("hub.topic"),
    )
    LOGGER.info(
        "%s: received status-request id=%s -> sent status-response",
        ctx.config.product_name,
        correlation_id.strip(),
    )


async def run(config: ResourceServerConfig, handlers: ResourceServerHandlers) -> int:
    """Run until cancelled. Returns ``0`` on clean stop, ``1`` if hub connect failed."""
    client = _build_client(config)
    hub_def = resolve_hub_preset(config.use_local_hub)

    def on_state(state: str, _detail: Optional[Dict[str, Any]] = None) -> None:
        LOGGER.info("%s: connection state: %s", config.product_name, state)

    client.on_connection_state_change(on_state)

    loop = asyncio.get_running_loop()
    hub_connection = _StandaloneHubConnection(client, loop)
    register_connection(config.product_name, hub_connection)
    ctx = ResourceServerContext(client, config, loop, hub_connection)
    connected = False
    exit_code = 0

    try:
        await _connect(client, config)
        connected = True
        while True:
            message = await client.message_queue.get()
            hub_event = hub_event_name(message)

            if hub_event in (_DICOM_SEND_EVENT, _NIFTI_SEND_EVENT):
                await _handle_send_event(ctx, handlers, message, hub_event)
                continue

            if is_request_event(hub_event):
                await _handle_status_request(ctx, handlers, message)
                continue

            LOGGER.info(
                "%s: received event=%s id=%s (ignored)",
                config.product_name,
                hub_event,
                message.get("id", ""),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if connected:
            raise
        exit_code = 1
        reason = format_connect_failure(exc)
        LOGGER.error(
            "%s: could not connect to hub %s at %s: %s",
            config.product_name,
            hub_def["name"],
            hub_def["hub_endpoint"],
            reason,
        )
        if config.use_local_hub:
            LOGGER.error(
                "%s: is the local Slicer hub running? "
                "Start it with: python hub/hub.py --port 2018",
                config.product_name,
            )
        else:
            LOGGER.error(
                "%s: check network access to the cloud hub, or pass --local "
                "for http://127.0.0.1:2018",
                config.product_name,
            )
    finally:
        unregister_connection(config.product_name)
        if connected:
            await client.close()
        else:
            # Avoid noisy Hub close/unsubscribe stacks when auth never succeeded.
            http = getattr(client, "_http", None)
            if http is not None and getattr(client, "_owns_http", False):
                await http.close()
                client._http = None
    return exit_code


def run_sync(
    config: ResourceServerConfig,
    handlers: ResourceServerHandlers,
    argv: Optional[List[str]] = None,
) -> None:
    """Blocking entry for product scripts (parses ``--local`` from ``argv`` / ``sys.argv``)."""
    args = parse_resource_server_args(argv)
    config = ResourceServerConfig(
        product_name=config.product_name,
        topic=config.topic,
        actors=list(config.actors),
        events=list(config.events),
        user_name=config.user_name,
        product_version=config.product_version,
        auto_reconnect=config.auto_reconnect,
        use_local_hub=args.local,
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    hub_def = resolve_hub_preset(config.use_local_hub)
    LOGGER.info(
        "%s: starting (hub=%s, endpoint=%s)",
        config.product_name,
        hub_def["name"],
        hub_def["hub_endpoint"],
    )
    try:
        exit_code = asyncio.run(run(config, handlers))
    except KeyboardInterrupt:
        LOGGER.info("%s: stopped", config.product_name)
        return
    if exit_code:
        raise SystemExit(exit_code)
