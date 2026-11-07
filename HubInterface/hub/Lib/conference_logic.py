"""Conference host/participant topic helpers and hub-event fan-out."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Dict, List

from state import hub


def _conference_host_topic(conf: Dict) -> str:
    """Host hub topic; legacy records may use ``user``."""
    return str(conf.get("hostTopic") or conf.get("user") or "").strip()


def _normalize_conference_topics(raw) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen = set()
    out: List[str] = []
    for entry in raw:
        topic = str(entry).strip()
        if topic and topic != "*" and topic not in seen:
            seen.add(topic)
            out.append(topic)
    return out


def _conference_participant_topics(conf: Dict) -> List[str]:
    host_topic = _conference_host_topic(conf)
    attendee_topics = _normalize_conference_topics(conf.get("topics", []))
    if not host_topic:
        return attendee_topics
    return [host_topic] + [t for t in attendee_topics if t != host_topic]


def _conference_participant_subscribers(conf: Dict) -> List[str]:
    subscriber_names: List[str] = []
    for participant_topic in _conference_participant_topics(conf):
        for sub in hub.subscriptions:
            if sub.get("topic") == participant_topic and sub.get("channel") == "websocket":
                name = str(sub.get("subscriber") or "unknown").strip()
                if name and name not in subscriber_names:
                    subscriber_names.append(name)
                break
    return subscriber_names


def _host_user_name_for_topic(topic: str, explicit: str = "") -> str:
    """Best-effort display name for the conference host."""
    name = str(explicit or "").strip()
    if name:
        return name
    topic = str(topic or "").strip()
    if not topic:
        return ""
    auth_entry = hub.authentications.get(topic)
    if isinstance(auth_entry, dict):
        candidate = str(auth_entry.get("user_name") or "").strip()
        if candidate:
            return candidate
    return ""


async def _send_conference_hub_event(
    event_name: str,
    host_topic: str,
    participant_topics: List[str],
    context: Dict,
) -> None:
    notification = {
        "timestamp": datetime.now().isoformat(),
        "id": str(uuid.uuid4()),
        "event": {
            "hub.topic": host_topic or "",
            "hub.event": event_name,
            "context": context,
        },
    }
    message_json = json.dumps(notification)
    sent_endpoints = set()
    for participant_topic in participant_topics:
        for sub in hub.subscriptions:
            if sub.get("topic") != participant_topic or sub.get("channel") != "websocket":
                continue
            endpoint = sub.get("websocket_endpoint")
            if (
                not endpoint
                or endpoint not in hub.websocket_connections
                or endpoint in sent_endpoints
            ):
                continue
            try:
                ws = hub.websocket_connections[endpoint]
                await ws.send_text(message_json)
                sent_endpoints.add(endpoint)
                hub.log(
                    f"Sent {event_name} to participant: {sub.get('subscriber')}"
                )
            except Exception as e:
                hub.log(f"{event_name} WebSocket error: {e}")
