"""Per-row Slicer hub connection (daemon thread + SlicerHubClient)."""

from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from hub_client import (
    SlicerHubClient,
    _context_files_from_event,
    data_type_from_event_name,
    dicom_send_byte_length,
    dicom_send_file_name,
    has_pending_payload,
    hub_event_name,
    is_request_event,
    normalize_data_type,
    request_context,
    binary_batch_files_pending_stats,
)

LOGGER = logging.getLogger("HubInterface.ResourceServerHub")
LOGGER.setLevel(logging.INFO)


def _short_caller_stack(skip: int = 2, depth: int = 6) -> str:
    """Compact stack of recent Python callers (newest last)."""
    frames = traceback.extract_stack()[:-skip]
    frames = frames[-depth:]
    lines = []
    for frame in frames:
        path = frame.filename.replace("\\", "/").rsplit("/", 2)
        short = "/".join(path[-2:]) if len(path) > 1 else frame.filename
        lines.append(f"  {short}:{frame.lineno} {frame.name}")
    return "\n".join(lines) if lines else "  (no caller frames)"


_active_connections: List["ResourceServerHubConnection"] = []


def format_connect_failure(exc: BaseException) -> str:
    """Short, user-facing reason for a failed hub connect."""
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


def disconnect_all_active_connections() -> None:
    for conn in list(_active_connections):
        conn.disconnectHub()


class ResourceServerHubConnection:
    """One resource-server row's hub session."""

    def __init__(self, post_ui: Callable[[Callable[[], None]], None]) -> None:
        self._post_ui = post_ui
        self._hub_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._want_hub_unsubscribe = False
        self._hub_subscribed = False
        self._hub_live = False
        self._client: Optional[SlicerHubClient] = None
        self._status_callback: Optional[
            Callable[[str, Optional[Dict[str, Any]]], None]
        ] = None
        self._last_imaging_study_context: List[Any] = []
        self._hub_name: str = ""
        self._product_name: str = ""
        self._product_version: str = ""
        self._script_path: str = ""
        self._resource_server_config: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connect_failed = False
        self._message_count = 0

    def schedule_publish(self, hub_message: Dict[str, Any]) -> None:
        if not self._client or not self._loop or self._stop_event.is_set():
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

    def _schedule_resource_server_warmup(self) -> None:
        if self._resource_server_config is None or self._stop_event.is_set():
            return
        cfg = self._resource_server_config

        async def _run_warmup() -> None:
            from .ResourceServers import run_resource_server_warmup_on_connect

            await asyncio.to_thread(run_resource_server_warmup_on_connect, cfg)

        asyncio.create_task(_run_warmup())

    def get_connection_summary(self) -> str:
        if not self.isHubConnected():
            return ""
        from .ResourceServers import TOPIC, _

        return _(
            f"Connected (hub={self._hub_name}, topic={TOPIC}, "
            f"product={self._product_name})"
        )

    def isHubThreadRunning(self) -> bool:
        return self._hub_thread is not None and self._hub_thread.is_alive()

    def isHubConnected(self) -> bool:
        return self.isHubThreadRunning() and self._hub_live

    def get_message_count(self) -> int:
        return self._message_count

    def connectHub(
        self,
        hub_name: str,
        product_name: str,
        product_version: str,
        script_path: str,
        resource_server_config: Any,
        status_callback: Callable[[str, Optional[Dict[str, Any]]], None],
    ) -> None:
        if self.isHubThreadRunning():
            LOGGER.warning("Slicer hub thread already running for this resource server")
            return

        self._hub_name = hub_name
        self._product_name = product_name
        self._product_version = product_version
        self._script_path = script_path
        self._resource_server_config = resource_server_config
        self._status_callback = status_callback
        self._want_hub_unsubscribe = False
        self._hub_subscribed = False
        self._hub_live = False
        self._connect_failed = False
        self._message_count = 0
        self._stop_event.clear()

        if self not in _active_connections:
            _active_connections.append(self)

        self._hub_thread = threading.Thread(
            target=self._hub_thread_main,
            name=f"Hub-{product_name}",
            daemon=True,
        )
        self._hub_thread.start()

    def disconnectHub(self) -> None:
        LOGGER.info(
            "disconnectHub called product=%s subscribed=%s caller=\n%s",
            self._product_name,
            self._hub_subscribed,
            _short_caller_stack(),
        )
        self._want_hub_unsubscribe = True
        self._stop_event.set()
        if self._hub_thread:
            self._hub_thread.join(timeout=20.0)
            self._hub_thread = None
        self._client = None
        self._hub_subscribed = False
        self._hub_live = False
        self._want_hub_unsubscribe = False
        self._status_callback = None
        if self in _active_connections:
            _active_connections.remove(self)
        from .provider_runtime import unregister_connection

        unregister_connection(self._product_name)

    def _hub_thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._hub_async_main())
        except Exception as exc:
            if not self._connect_failed:
                reason = format_connect_failure(exc)
                LOGGER.warning(
                    "Cannot connect product=%s hub=%s: %s",
                    self._product_name,
                    self._hub_name,
                    reason,
                )
                self._emit_status("failed", {"reason": reason})
        finally:
            self._loop = None
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _emit_status(
        self, state: str, detail: Optional[Dict[str, Any]] = None
    ) -> None:
        callback = self._status_callback
        if not callback:
            return

        def run() -> None:
            try:
                callback(state, detail)
            except Exception as exc:
                LOGGER.warning("status callback error: %s", exc)

        self._post_ui(run)

    async def _dispatch_resource_server_on_message(self, message: Dict[str, Any]) -> None:
        from .ResourceServers import (
            build_idc_claude_payload,
            resource_server_status_payload,
            run_resource_server_on_message,
        )

        if self._resource_server_config is None:
            return

        hub_event = hub_event_name(message)
        if is_request_event(hub_event):
            context = request_context(message)
            data_type = context.get("dataType")
            if not isinstance(data_type, str) or not data_type.strip():
                data_type = data_type_from_event_name(hub_event)
            if normalize_data_type(data_type) == normalize_data_type("STATUS"):
                client = self._client
                correlation_id = context.get("id")
                if (
                    client is not None
                    and isinstance(correlation_id, str)
                    and correlation_id.strip()
                ):
                    event = message.get("event") or {}
                    payload = resource_server_status_payload(
                        self._resource_server_config, self._product_name
                    )
                    client.send_hub_request_response(
                        correlation_id.strip(),
                        "STATUS",
                        payload,
                        event.get("hub.topic"),
                    )
                    LOGGER.info(
                        "sent status-response id=%s product=%s items=%d",
                        correlation_id.strip(),
                        self._product_name,
                        len(payload.get("items") or []),
                    )
                return
            if normalize_data_type(data_type) == normalize_data_type("IDC-CLAUDE"):
                client = self._client
                correlation_id = context.get("id")
                if (
                    client is not None
                    and isinstance(correlation_id, str)
                    and correlation_id.strip()
                ):
                    event = message.get("event") or {}
                    payload = await asyncio.to_thread(
                        build_idc_claude_payload,
                        self._resource_server_config,
                        context,
                    )
                    client.send_hub_request_response(
                        correlation_id.strip(),
                        "IDC-CLAUDE",
                        payload,
                        event.get("hub.topic"),
                    )
                    study_count = len(payload.get("studies") or [])
                    LOGGER.info(
                        "sent idc-claude-response id=%s product=%s studies=%d",
                        correlation_id.strip(),
                        self._product_name,
                        study_count,
                    )
                return

        event = message.get("event") or {}
        hub_event = event.get("hub.event")
        if hub_event in ("dicom-send", "nifti-send", "idc-claude-send"):
            # Offload staging and resource-server handlers so the hub asyncio loop can
            # process WebSocket ping/pong and reads during large transfers.
            await asyncio.to_thread(
                run_resource_server_on_message, self._resource_server_config, message
            )
            return

        def run() -> None:
            run_resource_server_on_message(self._resource_server_config, message)

        self._post_ui(run)

    async def _hub_async_main(self) -> None:
        from .ResourceServers import (
            DEFAULT_PRODUCT_VERSION,
            TOPIC,
            build_hub_client,
            subscribe_events_for_resource_server,
        )

        resource_server_cfg = self._resource_server_config
        if resource_server_cfg is None:
            from .ResourceServers import ResourceServerConfig

            resource_server_cfg = ResourceServerConfig(
                self._hub_name,
                self._product_name,
                self._product_version or DEFAULT_PRODUCT_VERSION,
                "",
                self._script_path or "",
            )
        subscribe_events = subscribe_events_for_resource_server(resource_server_cfg)
        LOGGER.debug(
            "Slicer hub subscribe product=%s script=%s hub.events=%s",
            self._product_name,
            getattr(resource_server_cfg, "script_path", self._script_path),
            ",".join(subscribe_events),
        )
        self._client = build_hub_client(
            self._hub_name,
            self._product_name,
            self._product_version or DEFAULT_PRODUCT_VERSION,
            events=subscribe_events,
        )

        def on_state(state: str, detail: Optional[Dict[str, Any]] = None) -> None:
            LOGGER.debug(
                "Hub connection state product=%s: %s",
                self._product_name,
                state,
            )
            if state == "connected":
                self._hub_live = True
            elif state in ("disconnected", "reconnecting", "error"):
                self._hub_live = False
            if (
                not self._hub_subscribed
                and state in ("disconnected", "error", "reconnecting")
            ):
                return
            self._emit_status(state, detail)

        self._client.on_connection_state_change(on_state)

        try:
            if self._stop_event.is_set():
                return

            auth = await self._client.authenticate()
            LOGGER.debug(
                "authenticated product=%s user_name=%s",
                self._product_name,
                auth.get("user_name"),
            )
            if self._stop_event.is_set():
                return

            code = auth.get("code")
            if not code:
                raise RuntimeError("authenticate did not return a code")
            if not await self._client.get_token(code):
                raise RuntimeError("token exchange failed")
            if self._stop_event.is_set():
                return

            status = await self._client.subscribe()
            if status != 202:
                if status == 0:
                    raise RuntimeError(
                        "subscribe failed (hub unreachable or network error)"
                    )
                raise RuntimeError(f"subscribe failed with HTTP {status}")
            self._hub_subscribed = True
            LOGGER.debug(
                "subscribed hub=%s topic=%s product=%s hub_endpoint=%s",
                self._hub_name,
                TOPIC,
                self._product_name,
                self._client._hub.hub_endpoint if self._client else "",
            )
            from .provider_runtime import register_connection

            register_connection(self._product_name, self)
            self._schedule_resource_server_warmup()

            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        self._client.message_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                self._message_count += 1
                self._emit_status(
                    "connected", {"message_count": self._message_count}
                )

                event = message.get("event") or {}
                hub_event = event.get("hub.event", "")
                # dicom-send / nifti-send: resolve bytes in onMessage (url or hub
                # payloadId GET), not via fetch_all_payloads on the message.
                # PNG/JPG *-request traffic is unchanged (inline binary responses).
                if has_pending_payload(event) and hub_event not in (
                    "dicom-send",
                    "nifti-send",
                ):
                    hub_endpoint = (
                        self._client._hub.hub_endpoint if self._client else ""
                    )
                    LOGGER.info(
                        "fetch_all_payloads start id=%s event=%s product=%s "
                        "hub=%s hub_endpoint=%s",
                        message.get("id"),
                        hub_event,
                        self._product_name,
                        self._hub_name,
                        hub_endpoint,
                    )
                    try:
                        message = await self._client.fetch_all_payloads(message)
                        event = message.get("event") or {}
                        hub_event = event.get("hub.event", "")
                    except Exception as exc:
                        LOGGER.warning(
                            "fetch_all_payloads failed id=%s event=%s product=%s "
                            "hub=%s hub_endpoint=%s: %s",
                            message.get("id"),
                            hub_event,
                            self._product_name,
                            self._hub_name,
                            hub_endpoint,
                            exc,
                        )
                        continue

                if hub_event == "dicom-send":
                    files = _context_files_from_event(event)
                    url_count, payload_files, chunk_count = binary_batch_files_pending_stats(
                        files
                    )
                    LOGGER.info(
                        "received dicom-send id=%s topic=%s file=%s bytes=%d "
                        "product=%s files=%d url=%d payloadFiles=%d chunks=%d "
                        "(download in onMessage)",
                        message.get("id"),
                        event.get("hub.topic", ""),
                        dicom_send_file_name(message),
                        dicom_send_byte_length(message),
                        self._product_name,
                        len(files),
                        url_count,
                        payload_files,
                        chunk_count,
                    )
                elif hub_event == "nifti-send":
                    files = _context_files_from_event(event)
                    url_count, payload_files, chunk_count = binary_batch_files_pending_stats(
                        files
                    )
                    LOGGER.info(
                        "received nifti-send id=%s topic=%s product=%s files=%d "
                        "url=%d payloadFiles=%d chunks=%d bytes=%d "
                        "(download in onMessage)",
                        message.get("id"),
                        event.get("hub.topic", ""),
                        self._product_name,
                        len(files),
                        url_count,
                        payload_files,
                        chunk_count,
                        dicom_send_byte_length(message),
                    )
                else:
                    LOGGER.info(
                        "received event=%s id=%s product=%s",
                        hub_event,
                        message.get("id"),
                        self._product_name,
                    )
                await self._dispatch_resource_server_on_message(message)
                self._track_imaging_study(message)
                self._maybe_auto_reply(message)
        except Exception as exc:
            self._connect_failed = True
            reason = format_connect_failure(exc)
            LOGGER.warning(
                "Cannot connect product=%s hub=%s: %s",
                self._product_name,
                self._hub_name,
                reason,
            )
            self._emit_status("failed", {"reason": reason})
        finally:
            from .provider_runtime import unregister_connection

            unregister_connection(self._product_name)
            if self._client:
                await self._client.close(hub_unsubscribe=self._want_hub_unsubscribe)
                self._client = None
            self._hub_subscribed = False
            self._hub_live = False
            if not self._connect_failed:
                self._emit_status("disconnected")

    def _maybe_auto_reply(self, message: dict) -> bool:
        if not self._client:
            return False

        event_name = hub_event_name(message)
        if not is_request_event(event_name):
            return False

        ctx = request_context(message)
        request_id = ctx.get("id")
        if not isinstance(request_id, str) or not request_id:
            return False

        data_type = ctx.get("dataType") or data_type_from_event_name(event_name)
        if data_type != "FHIRcastContext":
            LOGGER.info(
                "ignoring request event=%s dataType=%s",
                event_name,
                data_type,
            )
            return False

        if self._last_imaging_study_context:
            response_data = {
                "context.type": "ImagingStudy",
                "context": list(self._last_imaging_study_context),
            }
        else:
            from .ResourceServers import EMPTY_FHIRCAST_CONTEXT

            response_data = EMPTY_FHIRCAST_CONTEXT

        event = message.get("event") or {}
        self._client.send_hub_request_response(
            request_id,
            str(data_type),
            response_data,
            event.get("hub.topic"),
        )
        LOGGER.info("sent FHIRcastContext response for id=%s", request_id)
        return True

    def _track_imaging_study(self, message: dict) -> None:
        event = message.get("event") or {}
        if event.get("hub.event") != "imagingstudy-open":
            return
        context = event.get("context")
        if isinstance(context, list) and context:
            self._last_imaging_study_context = list(context)
            LOGGER.info(
                "cached imagingstudy-open context (%d items)", len(context)
            )
