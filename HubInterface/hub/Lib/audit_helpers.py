"""Publisher identity and audit message-parameter helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from request_parse import (
    _request_context_data_type,
    _request_topic_from_body,
)


def _optional_str_field(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _publisher_from_notification(notification: dict) -> Optional[Dict[str, Optional[str]]]:
    """Publisher identity from a publish notification (envelope subscriber fields)."""
    name = ""
    for key in ("subscriber.name", "subscriber", "publisher"):
        v = notification.get(key)
        if v is not None and str(v).strip():
            name = str(v).strip()
            break
    product = _optional_str_field(notification.get("subscriber.product.name"))
    if not name and not product:
        return None
    return {"name": name or None, "product": product}


def _publisher_name(publisher: Optional[Dict[str, Optional[str]]]) -> str:
    if not publisher or not isinstance(publisher, dict):
        return ""
    return (publisher.get("name") or "").strip()


def _publisher_for_hub_request(
    subscriber: str, product_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
    product = _optional_str_field(product_name)
    if product == "*":
        product = None
    return {
        "name": (subscriber or "").strip() or None,
        "product": product,
    }


def _audit_publisher_name(entry: Dict) -> str:
    """Display/filter name for a log row's publisher (supports legacy ``user`` rows)."""
    pub = entry.get("publisher")
    if isinstance(pub, dict):
        name = (pub.get("name") or "").strip()
        if name:
            return name
        product = (pub.get("product") or "").strip()
        if product:
            return product
    legacy = (entry.get("subscriber") or entry.get("user") or "").strip()
    return legacy


_AUDIT_MESSAGE_PARAM_ROOT_KEYS = (
    "id",
    "timestamp",
    "subscriber.name",
    "subscriber.product.name",
    "subscriber.actor",
    "target.actor",
    "target.product.name",
    "target.subscriber.name",
    "actor",
)

_AUDIT_CONTEXT_META_KEYS = ("dataType", "id", "level", "action")


def _audit_event_block_from_notification_event(event: Dict) -> Dict[str, Any]:
    event_block: Dict[str, Any] = {
        "hub.topic": event.get("hub.topic"),
        "hub.event": event.get("hub.event"),
    }
    ctx = event.get("context")
    if isinstance(ctx, dict):
        meta = {
            key: ctx[key]
            for key in _AUDIT_CONTEXT_META_KEYS
            if key in ctx and ctx[key] is not None
        }
        if meta:
            event_block["context"] = meta
    return event_block


def _audit_message_params_from_notification(notification: dict) -> Dict[str, Any]:
    """Hub wire parameters for admin (no event.context payload)."""
    params: Dict[str, Any] = {}
    for key in _AUDIT_MESSAGE_PARAM_ROOT_KEYS:
        if key in notification and notification[key] is not None:
            params[key] = notification[key]
    event = notification.get("event")
    if isinstance(event, dict):
        params["event"] = _audit_event_block_from_notification_event(event)
    return params


def _audit_message_params_from_hub_request(
    request_data: dict, request_event_name: str
) -> Dict[str, Any]:
    """Hub-request body parameters for admin (no collated response bodies)."""
    params: Dict[str, Any] = {}
    for key in _AUDIT_MESSAGE_PARAM_ROOT_KEYS:
        if key in request_data and request_data[key] is not None:
            params[key] = request_data[key]
    topic = _request_topic_from_body(request_data)
    data_type = _request_context_data_type(request_data, request_event_name) or ""
    request_id = str(request_data.get("id", "")).strip()
    if request_id and "id" not in params:
        params["id"] = request_id
    event_block: Dict[str, Any] = {
        "hub.topic": topic or None,
        "hub.event": request_event_name,
    }
    ctx_meta: Dict[str, Any] = {}
    if data_type:
        ctx_meta["dataType"] = data_type
    if request_id:
        ctx_meta["id"] = request_id
    if ctx_meta:
        event_block["context"] = ctx_meta
    params["event"] = event_block
    return params
