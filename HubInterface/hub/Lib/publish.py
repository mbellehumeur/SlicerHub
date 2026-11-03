"""Publish notification fan-out (WebSocket, WebSub, conference)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import urllib.request
from typing import Dict, Optional

from fastapi import HTTPException, Request
from hub_client import is_hub_binary_event

from audit_helpers import (
    _audit_message_params_from_notification,
    _publisher_from_notification,
    _publisher_name,
)
from binary_publish import (
    _parse_binary_batch_publish,
    _prepare_websocket_fanout_text,
)
from conference_logic import (
    _conference_host_topic,
    _conference_participant_topics,
    _normalize_conference_topics,
)
from state import hub
from subscriptions import (
    _subscription_accepts_target_actor,
    _subscription_accepts_target_product,
    _subscription_accepts_target_subscriber,
    _subscription_handles_event,
    _target_actor_from_payload,
    _target_product_name_from_payload,
    _target_subscriber_name_from_payload,
)


async def _handle_publish_notification(
    notification: dict,
    predecoded_binary_by_index: Optional[Dict[int, bytes]] = None,
):
    """Handle publish notification payload and broadcast to subscribers."""
    notification.pop("subscriber.product.version", None)
    message_id = notification.get("id", "unknown")
    event = notification.get("event", {})
    event_type = event.get("hub.event", "unknown")
    topic_name = event.get("hub.topic", "").strip()

    if not topic_name:
        raise HTTPException(status_code=400, detail="Missing event.hub.topic for publish request")

    publish_target_actor = _target_actor_from_payload(notification)
    publish_target_product = _target_product_name_from_payload(notification)
    publish_target_subscriber = _target_subscriber_name_from_payload(notification)
    target_log_parts = []
    if publish_target_product:
        target_log_parts.append(f"target.product.name={publish_target_product}")
    if publish_target_subscriber:
        target_log_parts.append(f"target.subscriber.name={publish_target_subscriber}")
    if publish_target_actor:
        target_log_parts.append(f"target.actor={publish_target_actor}")
    target_log_suffix = (
        f" ({', '.join(target_log_parts)})" if target_log_parts else ""
    )
    hub.log(
        f"Received message ID: {message_id} for topic {topic_name}, "
        f"event: {event_type}{target_log_suffix}"
    )

    # Broadcast to subscribers (sync method, but WebSocket sending needs async)
    context = event.get("context", {})

    # Full JSON for HMAC + WebSub; WebSocket uses payloadId rewrite when bytes stored
    notification_json = json.dumps(notification)
    ws_text = _prepare_websocket_fanout_text(
        notification,
        notification_json,
        predecoded_binary_by_index=predecoded_binary_by_index,
    )

    def _audit_context():
        if is_hub_binary_event(event_type) and ws_text != notification_json:
            try:
                return json.loads(ws_text).get("event", {}).get("context", context)
            except Exception:
                return {"hub.event": event_type, "payloadId": "(stored)"}
        return context

    audit_ctx = _audit_context()
    publisher = _publisher_from_notification(notification)
    publisher_subscriber = _publisher_name(publisher)
    publish_message_params = _audit_message_params_from_notification(notification)

    hub.add_audit_log(
        subscriber=publisher_subscriber or topic_name,
        topic=topic_name,
        event_name=event_type,
        event_data=audit_ctx,
        direction="received",
        publisher=publisher,
        message_params=publish_message_params,
    )

    # Track endpoints that have already received the message to prevent duplicates
    sent_endpoints = set()
    
    # Send to matching subscriptions
    for sub in hub.subscriptions[:]:  # Copy to allow removal
        if not _subscription_handles_event(sub, topic_name, event_type):
            continue
        if not _subscription_accepts_target_actor(sub, publish_target_actor):
            continue
        if not _subscription_accepts_target_product(sub, publish_target_product):
            continue
        if not _subscription_accepts_target_subscriber(
            sub, publish_target_subscriber
        ):
            continue

        secret = sub.get("secret", "")
        channel = sub.get("channel", "websub")
        
        # Calculate HMAC
        hmac_sig = ""
        if secret:
            hmac_sig = hmac.new(secret.encode(), notification_json.encode(), hashlib.sha256).hexdigest()
        
        if channel == "websocket":
            sub_name = (sub.get("subscriber") or "").strip()
            if (
                publisher_subscriber
                and sub_name.lower() == publisher_subscriber.lower()
            ):
                continue
            # WebSocket delivery - async
            endpoint = sub.get("websocket_endpoint")
            if endpoint and endpoint in hub.websocket_connections:
                try:
                    websocket = hub.websocket_connections[endpoint]
                    await websocket.send_text(ws_text)
                    if (
                        is_hub_binary_event(event_type)
                        and ws_text != notification_json
                    ):
                        hub.log(
                            f"Sent WebSocket message to {sub.get('subscriber')} "
                            f"via endpoint {endpoint} mode=payloadId "
                            f"event={event_type}"
                        )
                    else:
                        hub.log(
                            f"Sent WebSocket message to {sub.get('subscriber')} "
                            f"via endpoint {endpoint}"
                        )
                    # Track this endpoint as having received the message
                    sent_endpoints.add(endpoint)
                    # Log sent message
                    hub.add_audit_log(
                        subscriber=sub.get("subscriber", "unknown"),
                        topic=topic_name,
                        event_name=event_type,
                        event_data=audit_ctx,
                        direction="sent",
                        publisher=publisher,
                        message_params=publish_message_params,
                    )
                except Exception as e:
                    hub.log(f"WebSocket send error for {endpoint}: {type(e).__name__}: {e}")
                    hub.log(f"Removing failed WebSocket connection and subscription")
                    if endpoint in hub.websocket_connections:
                        del hub.websocket_connections[endpoint]
                    if sub in hub.subscriptions:
                        removed_copy = hub._copy_subscription_record(sub)
                        hub.subscriptions.remove(sub)
                        await hub.broadcast_subscription_removed(
                            [removed_copy], "send-failure"
                        )
            else:
                if not endpoint:
                    hub.log(f"WebSocket endpoint not set for subscription: {sub.get('subscriber')}")
                else:
                    hub.log(f"WebSocket not bound for subscription: {sub.get('subscriber')}")
        else:
            sub_name = (sub.get("subscriber") or "").strip()
            if (
                publisher_subscriber
                and sub_name.lower() == publisher_subscriber.lower()
            ):
                continue
            # WebSub delivery - HTTP POST to callback (can be async but using sync for now)
            callback = sub.get("callback")
            if callback:
                try:
                    req = urllib.request.Request(callback)
                    req.add_header("Content-Type", "application/json")
                    req.add_header("X-Hub-Signature", f"sha256={hmac_sig}")
                    req.data = notification_json.encode()
                    req.get_method = lambda: "POST"
                    
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=5))
                    hub.log(f"Sent WebSub notification to {callback}")
                    # Log sent message
                    hub.add_audit_log(
                        subscriber=sub.get("subscriber", "unknown"),
                        topic=topic_name,
                        event_name=event_type,
                        event_data=audit_ctx,
                        direction="sent",
                        publisher=publisher,
                        message_params=publish_message_params,
                    )
                except Exception as e:
                    hub.log(f"WebSub delivery error to {callback}: {e}")
    
    # Handle conferences - broadcast to attendees (skip if already sent)
    for conference in hub.conferences:
        conference_host = _conference_host_topic(conference)
        attendee_topics = _normalize_conference_topics(conference.get("topics", []))

        # Check if message is from any conference participant (host or attendee)
        is_participant = (conference_host == topic_name) or (topic_name in attendee_topics)

        if is_participant:
            # Send to all participants (host + all attendees)
            all_participants = _conference_participant_topics(conference)
            
            for participant_topic in all_participants:
                # Find subscriptions for participant
                for sub in hub.subscriptions:
                    if sub.get("topic") == participant_topic and sub.get("channel") == "websocket":
                        sub_name = (sub.get("subscriber") or "").strip()
                        if (
                            publisher_subscriber
                            and sub_name.lower() == publisher_subscriber.lower()
                        ):
                            continue
                        endpoint = sub.get("websocket_endpoint")
                        if endpoint and endpoint in hub.websocket_connections:
                            # Skip if already sent to this endpoint
                            if endpoint in sent_endpoints:
                                continue
                            try:
                                websocket = hub.websocket_connections[endpoint]
                                await websocket.send_text(ws_text)
                                hub.log(f"Sent conference message to participant: {participant_topic}")
                                # Track this endpoint as having received the message
                                sent_endpoints.add(endpoint)
                                # Log sent conference message
                                hub.add_audit_log(
                                    subscriber=sub.get("subscriber", "unknown"),
                                    topic=participant_topic,
                                    event_name=event_type,
                                    event_data=audit_ctx,
                                    direction="sent",
                                    publisher=publisher,
                                    message_params=publish_message_params,
                                )
                            except Exception as e:
                                hub.log(f"Conference WebSocket error: {e}")

    return {"status": "received"}


async def _handle_multipart_publish(request: Request):
    """Route multipart/related binary batch batch publish."""
    content_type = request.headers.get("content-type", "")
    ct_lower = content_type.lower()
    if "multipart/related" not in ct_lower:
        raise HTTPException(
            status_code=400,
            detail=(
                "binary publish requires multipart/related binary batch "
                "(application/dicom+json manifest + file parts)"
            ),
        )
    notification, blobs_by_file_index = await _parse_binary_batch_publish(request)
    # OIDC topic binding (Bearer already checked in hub_api).
    try:
        from http_util import _enforce_topic_match
        from oauth_tokens import validate_request_bearer

        identity = validate_request_bearer(request.headers.get("authorization"))
        event = notification.get("event") if isinstance(notification, dict) else {}
        pub_topic = ""
        if isinstance(event, dict):
            pub_topic = str(event.get("hub.topic") or "").strip()
        _enforce_topic_match(identity, pub_topic)
    except HTTPException:
        raise
    return await _handle_publish_notification(
        notification,
        predecoded_binary_by_index=blobs_by_file_index,
    )
