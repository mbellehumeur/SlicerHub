"""Conference HTTP routes."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, HTTPException, Request

from conference_logic import (
    _conference_host_topic,
    _conference_participant_subscribers,
    _conference_participant_topics,
    _host_user_name_for_topic,
    _normalize_conference_topics,
    _send_conference_hub_event,
)
from http_util import _enforce_bearer_auth, _parse_request_body, _serve_html_page
from state import hub

router = APIRouter()


@router.get("/api/hub/conference-topics")
@router.get("/api/hub/conference-topics/")
async def get_conference_topics():
    """Topics from active subscriptions (conference attendee picker).

    Returns ``[{topic, timezone}, ...]`` sorted by topic. ``timezone`` is the
    first non-empty ``client_info.timezone`` for that topic (IANA id), or ``\"\"``.
    """
    by_topic: Dict[str, str] = {}
    for sub in hub.get_subscriptions():
        topic = str(sub.get("topic") or "").strip()
        if not topic or topic == "*":
            continue
        if topic not in by_topic:
            by_topic[topic] = ""
        if by_topic[topic]:
            continue
        info = sub.get("client_info")
        if not isinstance(info, dict):
            continue
        tz = str(info.get("timezone") or "").strip()
        if tz:
            by_topic[topic] = tz
    return [
        {"topic": topic, "timezone": by_topic[topic]}
        for topic in sorted(by_topic.keys())
    ]


@router.get("/api/hub/conference")
async def get_conference():
    """Get all conferences"""
    return [
        {**conf, "participants": _conference_participant_subscribers(conf)}
        for conf in hub.conferences
    ]


@router.post("/api/hub/conference")
async def post_conference(request: Request):
    """Create a conference"""
    _enforce_bearer_auth(request)
    try:
        data = await _parse_request_body(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse request: {e}")

    host_topic = str(data.get("hostTopic") or data.get("user") or "").strip()
    title = str(data.get("title") or "").strip()
    attendee_topics = _normalize_conference_topics(data.get("topics", []))
    attendee_topics = [t for t in attendee_topics if t != host_topic]
    host_user_name = _host_user_name_for_topic(
        host_topic,
        str(data.get("hostUserName") or data.get("host_user_name") or "").strip(),
    )

    if not host_topic:
        raise HTTPException(status_code=400, detail="hostTopic is required")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    conference = {
        "hostTopic": host_topic,
        "sceneLeaderTopic": host_topic,
        "title": title,
        "topics": attendee_topics,
    }
    hub.conferences.append(conference)
    hub.log(f"Conference created: {conference.get('title')} host={host_topic}")

    all_participant_topics = _conference_participant_topics(conference)
    subscriber_names = _conference_participant_subscribers(conference)

    await _send_conference_hub_event(
        "conference-start",
        host_topic,
        all_participant_topics,
        {
            "title": title,
            "hostTopic": host_topic,
            "sceneLeaderTopic": host_topic,
            "hostUserName": host_user_name,
            "participants": subscriber_names,
        },
    )

    # Send admin refresh command (rate limited)
    await hub.send_admin_refresh_command()

    return {"status": "created", "conference": conference}


@router.patch("/api/hub/conference/join")
@router.patch("/api/hub/conference/join/")
async def patch_conference_join(request: Request):
    """Add a topic to an existing conference (voluntary join from the client dialog)."""
    _enforce_bearer_auth(request)
    try:
        data = await _parse_request_body(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse request: {e}")

    host_topic = str(data.get("hostTopic") or data.get("user") or "").strip()
    join_topic = str(data.get("joinTopic") or data.get("topic") or "").strip()
    join_user_name = str(
        data.get("joinUserName") or data.get("join_user_name") or ""
    ).strip()

    if not host_topic:
        raise HTTPException(status_code=400, detail="hostTopic is required")
    if not join_topic:
        raise HTTPException(status_code=400, detail="joinTopic is required")
    if join_topic == host_topic:
        raise HTTPException(
            status_code=400, detail="joinTopic cannot be the conference host"
        )

    match = None
    for conf in hub.conferences:
        if _conference_host_topic(conf) == host_topic:
            match = conf
            break
    if not match:
        raise HTTPException(status_code=404, detail="Conference not found")

    topics = _normalize_conference_topics(match.get("topics", []))
    if join_topic not in topics:
        topics.append(join_topic)
        match["topics"] = topics
    if not match.get("hostTopic"):
        match["hostTopic"] = host_topic

    title = str(match.get("title") or "").strip()
    scene_leader = str(match.get("sceneLeaderTopic") or host_topic).strip() or host_topic
    host_user_name = _host_user_name_for_topic(host_topic, "")
    join_label = _host_user_name_for_topic(join_topic, join_user_name) or join_topic
    all_participant_topics = _conference_participant_topics(match)
    subscriber_names = _conference_participant_subscribers(match)

    hub.log(
        f"Topic {join_topic} joined conference {title or host_topic} (as {join_label})"
    )

    await _send_conference_hub_event(
        "conference-start",
        host_topic,
        all_participant_topics,
        {
            "title": title,
            "hostTopic": host_topic,
            "sceneLeaderTopic": scene_leader,
            "hostUserName": host_user_name,
            "participants": subscriber_names,
            "joinedTopic": join_topic,
            "joinedUserName": join_label,
        },
    )
    await hub.send_admin_refresh_command()
    return {"status": "joined", "conference": match}


@router.patch("/api/hub/conference/lead")
@router.patch("/api/hub/conference/lead/")
async def patch_conference_lead(request: Request):
    """Transfer scene-update leadership; conference owner (hostTopic) is unchanged."""
    _enforce_bearer_auth(request)
    try:
        data = await _parse_request_body(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse request: {e}")

    host_topic = str(data.get("hostTopic") or data.get("user") or "").strip()
    scene_leader_topic = str(
        data.get("sceneLeaderTopic") or data.get("leadTopic") or ""
    ).strip()
    leader_user_name = str(
        data.get("leaderUserName") or data.get("leader_user_name") or ""
    ).strip()

    if not host_topic:
        raise HTTPException(status_code=400, detail="hostTopic is required")
    if not scene_leader_topic:
        raise HTTPException(status_code=400, detail="sceneLeaderTopic is required")

    match = None
    for conf in hub.conferences:
        if _conference_host_topic(conf) == host_topic:
            match = conf
            break
    if not match:
        raise HTTPException(status_code=404, detail="Conference not found")

    participants = _conference_participant_topics(match)
    if scene_leader_topic not in participants:
        raise HTTPException(
            status_code=400,
            detail="sceneLeaderTopic must be a conference participant",
        )

    match["sceneLeaderTopic"] = scene_leader_topic
    if not match.get("hostTopic"):
        match["hostTopic"] = host_topic

    leader_label = _host_user_name_for_topic(scene_leader_topic, leader_user_name)
    if not leader_label:
        leader_label = scene_leader_topic
    title = str(match.get("title") or "").strip()

    await _send_conference_hub_event(
        "conference-lead",
        host_topic,
        participants,
        {
            "title": title,
            "hostTopic": host_topic,
            "sceneLeaderTopic": scene_leader_topic,
            "leaderUserName": leader_label,
            "leaderLabel": leader_label,
            "participants": _conference_participant_subscribers(match),
        },
    )
    hub.log(
        f"Conference lead: {title!r} sceneLeader={scene_leader_topic} host={host_topic}"
    )
    await hub.send_admin_refresh_command()
    return {"status": "updated", "conference": match}


@router.delete("/api/hub/conference")
async def delete_conference(request: Request):
    """End a conference (host) or remove one attendee topic (leave)."""
    _enforce_bearer_auth(request)
    try:
        data = await _parse_request_body(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse request: {e}")

    host_topic = str(data.get("hostTopic") or data.get("user") or "").strip()
    leave_topic = str(data.get("leaveTopic") or "").strip()

    if not host_topic:
        raise HTTPException(status_code=400, detail="hostTopic is required")

    removed = []
    updated = []
    for conf in hub.conferences[:]:
        if _conference_host_topic(conf) != host_topic:
            continue

        conf_title = str(conf.get("title") or "").strip()

        if leave_topic:
            topics = _normalize_conference_topics(conf.get("topics", []))
            if leave_topic not in topics:
                break
            conf["topics"] = [t for t in topics if t != leave_topic]
            remaining = _normalize_conference_topics(conf.get("topics", []))
            hub.log(
                f"Attendee {leave_topic} left conference {conf_title or host_topic}"
            )
            if not remaining:
                # No attendees left — end the conference for everyone.
                hub.conferences.remove(conf)
                removed.append(conf)
                hub.log(
                    f"Conference ended (no attendees left): {conf_title or host_topic}"
                )
                await _send_conference_hub_event(
                    "conference-end",
                    host_topic,
                    [host_topic, leave_topic],
                    {
                        "title": conf_title,
                        "hostTopic": host_topic,
                        "reason": "empty",
                    },
                )
            else:
                updated.append(conf)
                # Notify host + remaining attendees (roster refresh) and the leaver.
                notify_topics = _conference_participant_topics(conf)
                if leave_topic not in notify_topics:
                    notify_topics = notify_topics + [leave_topic]
                await _send_conference_hub_event(
                    "conference-end",
                    host_topic,
                    notify_topics,
                    {
                        "title": conf_title,
                        "hostTopic": host_topic,
                        "reason": "attendee-left",
                        "leaveTopic": leave_topic,
                    },
                )
            break

        all_participant_topics = _conference_participant_topics(conf)
        hub.conferences.remove(conf)
        removed.append(conf)
        hub.log(f"Conference ended: {conf_title or host_topic}")
        await _send_conference_hub_event(
            "conference-end",
            host_topic,
            all_participant_topics,
            {
                "title": conf_title,
                "hostTopic": host_topic,
                "reason": "host-ended",
            },
        )
        break

    if len(removed) > 0 or len(updated) > 0:
        await hub.send_admin_refresh_command()

    return {"removed": len(removed), "updated": len(updated)}


@router.get("/api/hub/conference-client")
@router.get("/api/hub/conference-client/")
async def get_conference_client():
    """Get conference client page"""
    return _serve_html_page("conference-client.html")
