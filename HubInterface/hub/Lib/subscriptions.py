"""Subscription matching, actor/product filters, and log helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _summarize_websocket_message(message: Any, *, max_len: int = 200) -> str:
    """Short summary for WebSocket traffic (avoid logging full payloads)."""
    if not isinstance(message, dict):
        text = str(message)
        return text if len(text) <= max_len else text[:max_len] + "..."

    msg_type = message.get("type")
    if msg_type in ("ping", "pong"):
        return f"type={msg_type}"

    event = message.get("event")
    if isinstance(event, dict):
        hub_event = event.get("hub.event", "")
        context = event.get("context")
        request_id = ""
        if isinstance(context, dict) and context.get("id"):
            request_id = f" id={context.get('id')}"
        sub = message.get("subscriber.name") or message.get("subscriber") or ""
        sub_part = f" subscriber={sub}" if sub else ""
        return f"hub.event={hub_event}{request_id}{sub_part}"

    if msg_type:
        return f"type={msg_type}"

    keys = ",".join(sorted(message.keys())[:8])
    return f"keys=[{keys}]"

def _subscription_topic_matches(sub_topic: str, event_topic: str) -> bool:
    """True when a subscription should receive publishes for ``event_topic``."""
    sub_topic = (sub_topic or "").strip()
    event_topic = (event_topic or "").strip()
    if sub_topic == "*":
        return bool(event_topic)
    return event_topic == sub_topic


def _subscription_handles_event(sub: Dict, topic_name: str, event_type: str) -> bool:
    """Topic plus event filter — same eligibility as delivery (before channel / echo checks)."""
    if not _subscription_topic_matches(sub.get("topic", ""), topic_name):
        return False
    subscribed_events = (sub.get("events") or "").lower()
    et = event_type.lower()
    return et in subscribed_events or "*" in subscribed_events


def _actor_to_text(actor: Any) -> str:
    if isinstance(actor, str):
        return actor.strip()
    if isinstance(actor, dict):
        candidate = actor.get("id") or actor.get("key") or actor.get("name")
        return str(candidate).strip() if candidate is not None else ""
    if actor is None:
        return ""
    return str(actor).strip()


def _normalize_target_actor(value: Any) -> str:
    """Return destination filter keyword, or empty for missing / ``*`` (no filter)."""
    text = _actor_to_text(value)
    if text == "*":
        return ""
    return text


def _subscription_actor_list(sub: Dict) -> List[str]:
    actors = sub.get("actors") or []
    if isinstance(actors, list):
        return [str(a).strip() for a in actors if str(a).strip()]
    text = str(actors).strip()
    return [text] if text else []


def _response_identity_from_ws_message(
    message: Any,
    *,
    endpoint: Optional[str] = None,
    subscriptions: Optional[List[Dict]] = None,
) -> Dict[str, str]:
    """Subscriber and actor from a bind-socket *-response envelope."""
    if not isinstance(message, dict):
        return {"subscriber": "", "actor": ""}
    subscriber_name = str(
        message.get("subscriber.name") or message.get("subscriber") or ""
    ).strip()
    actor_name = _actor_to_text(message.get("actor"))
    event = message.get("event")
    if isinstance(event, dict):
        context = event.get("context")
        if not actor_name and isinstance(context, dict):
            actor_name = _actor_to_text(context.get("actor"))
        if not subscriber_name:
            sender = event.get("sender")
            if isinstance(sender, dict):
                subscriber_name = str(sender.get("subscriber", "")).strip()
    if not subscriber_name and endpoint:
        if subscriptions is not None:
            subs = subscriptions
        else:
            from state import hub as _hub

            subs = _hub.get_subscriptions()
        for sub in subs:
            if sub.get("websocket_endpoint") == endpoint:
                subscriber_name = str(sub.get("subscriber", "")).strip()
                break
    return {"subscriber": subscriber_name, "actor": actor_name}


def _subscription_accepts_target_actor(sub: Dict, target_actor: str) -> bool:
    if not target_actor:
        return True
    return target_actor in _subscription_actor_list(sub)


def _subscription_product_name(sub: Dict) -> str:
    client_info = sub.get("client_info") or {}
    if not isinstance(client_info, dict):
        return ""
    product = client_info.get("productName")
    if product is None:
        return ""
    return str(product).strip()


def _target_product_name_from_payload(payload: Dict) -> str:
    """Destination product filter (``target.product.name`` on publish/request)."""
    raw = payload.get("target.product.name")
    if raw is None:
        return ""
    return str(raw).strip()


def _target_product_filter_active(target_product_name: str) -> bool:
    return bool(target_product_name) and target_product_name != "*"


def _subscription_accepts_target_product(sub: Dict, target_product_name: str) -> bool:
    if not target_product_name or target_product_name == "*":
        return True
    return _subscription_product_name(sub) == target_product_name


def _target_subscriber_name_from_payload(payload: Dict) -> str:
    """Destination subscriber filter (``target.subscriber.name`` on publish)."""
    raw = payload.get("target.subscriber.name")
    if raw is None:
        return ""
    return str(raw).strip()


def _subscription_accepts_target_subscriber(
    sub: Dict, target_subscriber_name: str
) -> bool:
    if not target_subscriber_name or target_subscriber_name == "*":
        return True
    sub_name = str(sub.get("subscriber") or "").strip()
    return sub_name.lower() == target_subscriber_name.lower()


def _target_actor_from_payload(payload: Dict) -> str:
    """Destination filter from publish payload (``target.actor`` on envelope root)."""
    return _normalize_target_actor(payload.get("target.actor"))


def _target_actor_filter_from_request_body(request_data: Dict) -> str:
    """Hub request routing: ``target.actor`` only (``*`` = all roles; omit = all roles)."""
    if "target.actor" in request_data:
        return _normalize_target_actor(request_data.get("target.actor"))
    return ""


def _format_hub_events_display(hub_events: Any) -> str:
    """Human-readable hub.events for subscription INFO logs and admin alignment."""
    if hub_events is None:
        return "(none)"
    text = str(hub_events).strip()
    if not text:
        return "(empty)"
    if text == "*":
        return "*"
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) > 1:
        return "[" + ", ".join(parts) + "]"
    return text


def _subscription_log_message(
    action: str,
    *,
    subscriber_name: str,
    hub_topic: str,
    channel_type: str,
    hub_events: Any,
    subscriber_actors: List[str],
    subscriber_product: str = "",
    subscriber_version: str = "",
    websocket_endpoint: str = "",
) -> str:
    actors_display = (
        "[" + ", ".join(subscriber_actors) + "]" if subscriber_actors else "[]"
    )
    parts = [
        f"Subscription {action}: subscriber={subscriber_name}",
        f"topic={hub_topic or '(empty)'}",
        f"channel={channel_type}",
        f"events={_format_hub_events_display(hub_events)}",
        f"actors={actors_display}",
    ]
    if subscriber_product:
        parts.append(f"product={subscriber_product}")
    if subscriber_version:
        parts.append(f"version={subscriber_version}")
    if websocket_endpoint:
        parts.append(f"endpoint={websocket_endpoint}")
    return " ".join(parts)
