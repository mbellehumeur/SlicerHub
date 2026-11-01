"""Typed hub-request fan-out and collated HTTP response."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from hub_client import is_request_event

from audit_helpers import (
    _audit_message_params_from_hub_request,
    _optional_str_field,
    _publisher_for_hub_request,
)
from request_parse import (
    _hub_request_event_dict,
    _request_context_data_type,
    _request_hub_event_from_body,
    _request_timeout_seconds,
    _request_topic_from_body,
)
from state import hub
from subscriptions import (
    _actor_to_text,
    _subscription_accepts_target_product,
    _subscription_actor_list,
    _subscription_product_name,
    _subscription_topic_matches,
    _target_actor_filter_from_request_body,
    _target_product_filter_active,
    _target_product_name_from_payload,
)


async def _handle_hub_request(request_data: dict):
    """Dispatch a typed request to all matching subscribers and return collated responses.

    Invoked from ``POST /api/hub`` when ``event.hub.event`` ends with ``-request``.
    Topic filtering uses ``event.hub.topic``. Optional ``event.context.dataType`` is
    forwarded on the WebSocket fan-out.

    Matches subscriptions by ``(topic, target.actor[, target.product.name])`` (``target.actor``
    ``*`` or omitted = all roles on topic). The requester's ``subscriber.name`` is never a
    dispatch target. Sends the client's ``hub.event`` to each connected match and waits for the
    matching ``<datatype>-response`` events on the bind WebSocket. Timeout defaults to
    ``HUB_REQUEST_TIMEOUT_SECONDS`` (2s); use ``event.context.timeoutSeconds`` (max 300) or
    ``idc-claude-request`` (180s min).
    """
    if not isinstance(request_data, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    subscriber = str(request_data.get("subscriber.name", "")).strip()
    request_event_name = _request_hub_event_from_body(request_data)
    topic_param = _request_topic_from_body(request_data)
    data_type = _request_context_data_type(request_data, request_event_name) or ""
    subscriber_actor_value = request_data.get("subscriber.actor")
    requested_actor = _actor_to_text(subscriber_actor_value)
    filter_actor = _target_actor_filter_from_request_body(request_data)
    requested_product_name = _target_product_name_from_payload(request_data)
    product_filter_active = _target_product_filter_active(requested_product_name)
    request_message_params = _audit_message_params_from_hub_request(
        request_data, request_event_name
    )

    if not subscriber:
        raise HTTPException(
            status_code=400, detail="Missing 'subscriber.name' parameter"
        )

    if not request_event_name or not is_request_event(request_event_name):
        raise HTTPException(
            status_code=400,
            detail="event.hub.event must be a *-request event name",
        )

    request_id = str(request_data.get("id", "")).strip()
    if not request_id:
        raise HTTPException(
            status_code=400, detail="Missing 'id' on hub request body"
        )

    # Find requester subscriptions first (caller identity; topic only, not hub.events).
    requester_matches = []
    for sub in hub.get_subscriptions():
        sub_name = sub.get("subscriber", "").strip()
        if sub_name != subscriber:
            continue
        if topic_param and not _subscription_topic_matches(
            sub.get("topic", ""), topic_param
        ):
            continue
        requester_matches.append(sub)

    subscriber_exists = bool(requester_matches)
    subscriber_connected = False
    for sub in requester_matches:
        websocket_endpoint = sub.get("websocket_endpoint")
        if websocket_endpoint and websocket_endpoint in hub.websocket_connections:
            subscriber_connected = True
            break

    # Find ALL connected dispatch targets (topic, hub.events, actor[, product]) — same
    # eligibility as publish; subscriber topic ``*`` matches any request topic.
    # The requester never receives its own request (callers already know local state).
    target_subscriptions: List[Dict] = []
    target_exists = False
    target_any_match = False  # any subscription matched the (topic, actor[, product]) filter
    for sub in hub.get_subscriptions():
        if sub.get("subscriber", "").strip() == subscriber:
            continue
        if topic_param and not _subscription_topic_matches(
            sub.get("topic", ""), topic_param
        ):
            continue
        subscribed_events = (sub.get("events") or "").lower()
        request_et = request_event_name.lower()
        if request_et not in subscribed_events and "*" not in subscribed_events:
            continue
        if filter_actor and filter_actor not in _subscription_actor_list(sub):
            continue
        if product_filter_active and not _subscription_accepts_target_product(
            sub, requested_product_name
        ):
            continue
        target_any_match = True
        websocket_endpoint = sub.get("websocket_endpoint")
        if websocket_endpoint and websocket_endpoint in hub.websocket_connections:
            target_subscriptions.append(sub)
            target_exists = True

    target_connected = bool(target_subscriptions)

    audit_status = "not-dispatched"
    audit_error: Optional[str] = None
    audit_topic = topic_param
    responses_collected: List[Dict[str, Any]] = []
    expected_subscribers: List[str] = []
    missing_subscribers: List[str] = []
    timed_out = False
    dispatch_topics: Set[str] = set()
    dispatched_count = 0

    if not subscriber_exists:
        audit_status = "requester-not-found"
        hub.log(
            f"Hub request: requester subscriber '{subscriber}' not found"
            + (f" for topic '{topic_param}'" if topic_param else "")
        )
    elif not subscriber_connected:
        audit_status = "requester-not-connected"
        hub.log(
            f"Hub request: requester subscriber '{subscriber}' is not currently websocket-connected"
        )
    elif not target_any_match:
        audit_status = "target-not-found"
        hub.log(
            "Hub request: no subscription matched "
            f"event='{request_event_name}', target.actor='{filter_actor or '*'}'"
            + (f", topic='{topic_param}'" if topic_param else "")
            + (
                f", target.product.name='{requested_product_name}'"
                if product_filter_active
                else ""
            )
        )
    elif not target_connected:
        audit_status = "target-not-connected"
        hub.log(
            "Hub request: matched targets exist but none are currently websocket-connected"
        )

    if target_connected:
        # Build the rich pending-request record now so the WS handler can find it.
        subscriber_products: Dict[str, str] = {}
        expected_set: Set[str] = set()
        for sub in target_subscriptions:
            name = sub.get("subscriber", "").strip()
            if not name:
                continue
            expected_set.add(name)
            subscriber_products[name] = _subscription_product_name(sub)

        if topic_param:
            dispatch_topics.add(topic_param)

        completion_event = asyncio.Event()
        # Edge case: every match shared an empty subscriber name -> fall through
        # as if no targets matched.
        if not expected_set:
            audit_status = "target-not-found"
            hub.log("Hub request: matched targets had no subscriber names; aborting")
            target_connected = False
        else:
            hub.pending_requests[request_id] = {
                "actor": requested_actor or None,
                "target_actor": filter_actor or None,
                "product_name": requested_product_name or None,
                "data_type": data_type or None,
                "event_name": request_event_name,
                "expected": set(expected_set),
                "subscriber_products": subscriber_products,
                "responses": responses_collected,
                "seen_envelope_ids": set(),
                "completion": completion_event,
                "lock": asyncio.Lock(),
                "topic": topic_param,
                "started_at": datetime.now().isoformat(),
            }

            audit_topic = next(iter(dispatch_topics), topic_param)

            hub.log(
                "Hub request dispatch: "
                f"requester='{subscriber}', "
                f"targets={sorted(expected_set)}, "
                f"topic='{audit_topic}', "
                f"dataType='{data_type or ''}', "
                f"subscriber.actor='{requested_actor or ''}', "
                f"target.actor='{filter_actor or '*'}', "
                f"target.product.name='{requested_product_name or ''}', "
                f"event='{request_event_name}', "
                f"id='{request_id}'"
            )
            hub.add_audit_log(
                subscriber=subscriber,
                topic=audit_topic,
                event_name=request_event_name,
                event_data={
                    "id": request_id,
                    "status": "dispatched",
                    "dataType": data_type or None,
                    "requestedActor": requested_actor or None,
                    "requestedTargetActor": filter_actor or None,
                    "requestedProductName": requested_product_name or None,
                    "expectedCount": len(expected_set),
                    "targetSubscribers": sorted(expected_set),
                },
                direction="sent",
                publisher=_publisher_for_hub_request(subscriber, requested_product_name),
                message_params=request_message_params,
            )

            requester_product_name = _optional_str_field(
                request_data.get("subscriber.product.name")
            )
            if not requester_product_name:
                for req_sub in requester_matches:
                    requester_product_name = _optional_str_field(
                        _subscription_product_name(req_sub)
                    )
                    if requester_product_name:
                        break

            try:
                send_failures: List[str] = []
                for sub in target_subscriptions:
                    name = sub.get("subscriber", "").strip()
                    websocket_endpoint = sub.get("websocket_endpoint")
                    websocket = hub.websocket_connections.get(websocket_endpoint)
                    if not websocket:
                        send_failures.append(name)
                        continue
                    dispatch_topic = topic_param or sub.get("topic", "").strip()
                    req_event = _hub_request_event_dict(request_data)
                    req_ctx = req_event.get("context")
                    fan_out_context: Dict[str, Any] = (
                        dict(req_ctx) if isinstance(req_ctx, dict) else {}
                    )
                    fan_out_context["id"] = request_id
                    if data_type:
                        fan_out_context["dataType"] = data_type
                    notification = {
                        "timestamp": datetime.now().isoformat(),
                        "id": str(uuid.uuid4()),
                        "event": {
                            "hub.topic": dispatch_topic,
                            "hub.event": request_event_name,
                            "context": fan_out_context,
                        },
                    }
                    notification["subscriber.name"] = subscriber
                    if requester_product_name:
                        notification["subscriber.product.name"] = requester_product_name
                    if requested_actor:
                        notification["subscriber.actor"] = requested_actor
                    if filter_actor:
                        notification["target.actor"] = filter_actor
                    try:
                        await websocket.send_text(json.dumps(notification))
                        dispatched_count += 1
                    except Exception as send_err:
                        hub.log(
                            f"Hub request send failed for subscriber '{name}': "
                            f"{type(send_err).__name__}: {send_err}"
                        )
                        send_failures.append(name)

                # If any sends failed, drop those subscribers from the expected set
                # so we don't wait for responses they'll never produce.
                async with hub.pending_requests[request_id]["lock"]:
                    for failed_name in send_failures:
                        hub.pending_requests[request_id]["expected"].discard(failed_name)
                    if not hub.pending_requests[request_id]["expected"]:
                        completion_event.set()

                if dispatched_count == 0:
                    audit_status = "send-error"
                    audit_error = "Failed to send to all matched targets"
                else:
                    request_timeout_seconds = _request_timeout_seconds(request_data)
                    hub.log(
                        "Hub request wait: "
                        f"requester='{subscriber}', id='{request_id}', "
                        f"event='{request_event_name}', "
                        f"timeoutSeconds={request_timeout_seconds}"
                    )
                    try:
                        await asyncio.wait_for(
                            completion_event.wait(),
                            timeout=request_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        timed_out = True

                    record = hub.pending_requests.get(request_id, {}) or {}
                    async with record.get("lock") or asyncio.Lock():
                        responses_collected = list(record.get("responses") or [])
                        missing_subscribers = sorted(record.get("expected") or set())
                    expected_subscribers = sorted(expected_set)

                    if timed_out:
                        audit_status = "timeout"
                        audit_error = (
                            f"Timeout after {request_timeout_seconds}s waiting for responses"
                        )
                        hub.log(
                            "Hub request timeout: "
                            f"requester='{subscriber}', id='{request_id}', "
                            f"received={len(responses_collected)}/{len(expected_subscribers)}, "
                            f"missing={missing_subscribers}"
                        )
                    elif responses_collected:
                        audit_status = "response-received"
                    else:
                        audit_status = "no-response"

                    response_preview = ""
                    try:
                        response_preview = json.dumps(responses_collected)[:600]
                    except Exception:
                        response_preview = str(responses_collected)[:600]
                    hub.log(
                        "Hub request collated: "
                        f"requester='{subscriber}', "
                        f"id='{request_id}', "
                        f"count={len(responses_collected)}, "
                        f"timedOut={timed_out}, "
                        f"missing={missing_subscribers}, "
                        f"responses={response_preview[:200]}"
                    )
            except Exception as e:
                audit_status = "send-error"
                audit_error = f"{type(e).__name__}: {e}"
                hub.log(
                    "Hub request dispatch error: "
                    f"requester='{subscriber}', id='{request_id}', "
                    f"{type(e).__name__}: {e}"
                )

    if not expected_subscribers:
        expected_subscribers = sorted({sub.get("subscriber", "").strip() for sub in target_subscriptions if sub.get("subscriber")})

    if request_id:
        record = hub.pending_requests.get(request_id)
        if record:
            async with record.get("lock") or asyncio.Lock():
                responses_collected = list(record.get("responses") or [])
                missing_subscribers = sorted(record.get("expected") or set())
            if responses_collected and audit_status == "no-response":
                audit_status = "response-received"

    hub.add_audit_log(
        subscriber=subscriber,
        topic=audit_topic or topic_param,
        event_name=request_event_name,
        event_data={
            "id": request_id,
            "status": audit_status,
            "dataType": data_type or None,
            "requestedActor": requested_actor or None,
            "requestedTargetActor": filter_actor or None,
            "requestedProductName": requested_product_name or None,
            "expectedCount": len(expected_subscribers),
            "actualCount": len(responses_collected),
            "expectedSubscribers": expected_subscribers,
            "missingSubscribers": missing_subscribers,
            "timedOut": timed_out,
            "error": audit_error,
            "responses": responses_collected,
        },
        direction="received",
        publisher=_publisher_for_hub_request(subscriber, requested_product_name),
        message_params=request_message_params,
    )
    if request_id:
        # Drop pending state only after the audit row is written so a response
        # arriving between collation and logging cannot be lost from the log.
        hub.pending_requests.pop(request_id, None)
    await hub.send_admin_refresh_command()

    out: Dict[str, Any] = {
        "ok": audit_status in ("response-received", "no-response"),
        "id": request_id,
        "subscriber.name": subscriber,
        "dataType": data_type if data_type else None,
        "subscriber.actor": requested_actor or None,
        "target.actor": filter_actor or None,
        "exists": subscriber_exists,
        "connected": subscriber_connected,
        "topic": (next(iter(dispatch_topics)) if dispatch_topics else None),
        "endpoint": None,
        "targetExists": target_exists,
        "targetConnected": target_connected,
        "responses": responses_collected,
        "expected": expected_subscribers,
        "missing": missing_subscribers,
        "timedOut": timed_out,
    }
    if topic_param:
        out["requestedTopic"] = topic_param
    if requested_product_name:
        out["target.product.name"] = requested_product_name
    if audit_error:
        out["error"] = audit_error
    return out
