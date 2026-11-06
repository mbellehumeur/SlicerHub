"""Parse hub-request body fields (topic, event, timeout, dataType)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from hub_client import data_type_from_event_name

from env import HUB_REQUEST_TIMEOUT_SECONDS


_UPPERCASE_GET_REQUEST_DATA_TYPES = frozenset(
    {
        "pngthumbnail",
        "jpgthumbnail",
        "pngfullsize",
        "jpgfullsize",
    }
)


def _hub_request_event_dict(request_data: Dict) -> Dict[str, Any]:
    event = request_data.get("event")
    return event if isinstance(event, dict) else {}


def _request_hub_event_from_body(request_data: Dict) -> str:
    return str(_hub_request_event_dict(request_data).get("hub.event", "")).strip().lower()


def _request_topic_from_body(request_data: Dict) -> str:
    topic = _hub_request_event_dict(request_data).get("hub.topic")
    return str(topic).strip() if topic is not None and str(topic).strip() else ""


def _request_context_data_type(
    request_data: Dict, request_event_name: str
) -> Optional[str]:
    event = _hub_request_event_dict(request_data)
    context = event.get("context")
    if isinstance(context, dict):
        data_type = context.get("dataType")
        if data_type is not None and str(data_type).strip():
            return str(data_type).strip()
    base = data_type_from_event_name(request_event_name)
    if not base:
        return None
    if base in _UPPERCASE_GET_REQUEST_DATA_TYPES:
        return base.upper()
    return base


IDC_CLAUDE_REQUEST_TIMEOUT_SECONDS = 180.0
# Local DICOM export + multipart dicom-send often exceeds the snappy 2s default
# while Image Display is busy on the Slicer main thread.
DICOM_REQUEST_TIMEOUT_SECONDS = 120.0


def _request_timeout_seconds(request_data: Dict) -> float:
    """Per-request fan-out timeout; ``context.timeoutSeconds`` capped at 300s."""
    for container in (
        _hub_request_event_dict(request_data).get("context"),
        request_data.get("context"),
        request_data,
    ):
        if not isinstance(container, dict):
            continue
        raw = container.get("timeoutSeconds")
        if raw is not None:
            try:
                value = float(raw)
                if value > 0:
                    return min(value, 300.0)
            except (TypeError, ValueError):
                pass
    request_event = _request_hub_event_from_body(request_data)
    if request_event == "idc-claude-request":
        return max(HUB_REQUEST_TIMEOUT_SECONDS, IDC_CLAUDE_REQUEST_TIMEOUT_SECONDS)
    if request_event == "dicom-request":
        return max(HUB_REQUEST_TIMEOUT_SECONDS, DICOM_REQUEST_TIMEOUT_SECONDS)
    return HUB_REQUEST_TIMEOUT_SECONDS
