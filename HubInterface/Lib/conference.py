"""Slicer hub conference REST helpers (parity with vtk-js conference.js)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .ServiceProviders import HUBS

LOGGER = logging.getLogger("HubInterface.Conference")

HUB_CONFERENCE_POLL_MS = 30_000
HUB_CONFERENCE_EXIT_ACK_MS = 1200

HUB_CONFERENCE_TITLE_PRESETS = [
    "Test conference",
    "US annotations",
    "Tumor Board",
    "Case discussion",
    "Pedicle screw",
]


def hub_endpoint_for_name(hub_name: str) -> Optional[str]:
    hub_def = HUBS.get(hub_name)
    if not hub_def:
        return None
    endpoint = str(hub_def.get("hub_endpoint", "")).strip()
    return endpoint or None


def http_url_from_hub_endpoint(hub_endpoint: str) -> Optional[str]:
    trimmed = str(hub_endpoint or "").strip()
    if not trimmed:
        return None
    try:
        parsed = urlparse(trimmed)
        scheme = parsed.scheme or "http"
        if scheme == "ws":
            scheme = "http"
        elif scheme == "wss":
            scheme = "https"
        if not parsed.netloc:
            return None
        return f"{scheme}://{parsed.netloc}"
    except Exception:
        return None


def _conference_api_url(hub_endpoint: str, path: str) -> Optional[str]:
    origin = http_url_from_hub_endpoint(hub_endpoint)
    if not origin:
        return None
    return urljoin(origin, path)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw.strip():
            return None
        return json.loads(raw)


def normalize_conference_participants(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    result: List[str] = []
    for entry in raw:
        name = str(entry or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def conference_host_topic(conference: Optional[Dict[str, Any]]) -> str:
    if not conference:
        return ""
    return str(conference.get("hostTopic") or conference.get("user") or "").strip()


def is_cast_conference_host(topic: str, conference: Optional[Dict[str, Any]]) -> bool:
    host = conference_host_topic(conference)
    normalized_topic = str(topic or "").strip()
    if not normalized_topic or not host:
        return False
    return normalized_topic == host or normalized_topic.lower() == host.lower()


def is_cast_conference_participant(
    topic: str,
    subscriber_name: str,
    conference: Optional[Dict[str, Any]],
) -> bool:
    if not conference:
        return False
    normalized_topic = str(topic or "").strip()
    normalized_subscriber = str(subscriber_name or "").strip()
    host = conference_host_topic(conference)
    raw_topics = conference.get("topics")
    attendee_topics = (
        [str(value).strip() for value in raw_topics if str(value).strip()]
        if isinstance(raw_topics, list)
        else []
    )

    if normalized_topic:
        if normalized_topic == host:
            return True
        if normalized_topic in attendee_topics:
            return True
    if normalized_subscriber and normalized_subscriber == host:
        return True
    return False


def find_active_cast_conference(
    topic: str,
    subscriber_name: str,
    conferences: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for conference in conferences:
        if is_cast_conference_participant(topic, subscriber_name, conference):
            return conference
    return None


def fetch_conference_topics(hub_endpoint: str) -> List[str]:
    api_url = _conference_api_url(hub_endpoint, "/api/hub/conference-topics")
    if not api_url:
        return []
    try:
        data = _http_json(api_url)
        if not isinstance(data, list):
            return []
        topics: List[str] = []
        for entry in data:
            if isinstance(entry, dict):
                topic = str(entry.get("topic") or "").strip()
            else:
                topic = str(entry or "").strip()
            if topic and topic != "*":
                topics.append(topic)
        return topics
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("fetch_conference_topics failed: %s", exc)
        return []


def fetch_conferences(hub_endpoint: str) -> List[Dict[str, Any]]:
    api_url = _conference_api_url(hub_endpoint, "/api/hub/conference")
    if not api_url:
        return []
    try:
        data = _http_json(api_url)
        return data if isinstance(data, list) else []
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("fetch_conferences failed: %s", exc)
        return []


def create_cast_conference(
    hub_endpoint: str,
    host_topic: str,
    title: str,
    topics: List[str],
) -> None:
    api_url = _conference_api_url(hub_endpoint, "/api/hub/conference")
    if not api_url:
        raise ValueError("Invalid hub endpoint")
    try:
        _http_json(
            api_url,
            method="POST",
            body={
                "hostTopic": str(host_topic).strip(),
                "title": str(title).strip(),
                "topics": topics,
            },
        )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail or f"HTTP {exc.code}") from exc
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def delete_cast_conference(
    hub_endpoint: str,
    host_topic: str,
    leave_topic: Optional[str] = None,
) -> None:
    api_url = _conference_api_url(hub_endpoint, "/api/hub/conference")
    if not api_url:
        raise ValueError("Invalid hub endpoint")
    body: Dict[str, str] = {"hostTopic": str(host_topic).strip()}
    leave = str(leave_topic or "").strip()
    if leave:
        body["leaveTopic"] = leave
    try:
        _http_json(api_url, method="DELETE", body=body)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail or f"HTTP {exc.code}") from exc
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def resolve_cast_conference_state(
    hub_endpoint: str,
    topic: str,
    subscriber_name: str,
) -> Dict[str, Any]:
    conferences = fetch_conferences(hub_endpoint)
    match = find_active_cast_conference(topic, subscriber_name, conferences)
    return {
        "active": bool(match),
        "title": str(match.get("title") or "").strip() if match else "",
        "participants": normalize_conference_participants(
            match.get("participants") if match else []
        ),
    }
