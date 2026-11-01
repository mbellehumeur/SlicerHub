"""WebSocket /bind/{endpoint} for subscriber event delivery."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from hub_client import is_response_event
from oauth_tokens import HUB_REQUIRE_AUTH

from env import HUB_WS_KEEPALIVE, HUB_WS_KEEPALIVE_INTERVAL_SECONDS
from http_util import _bind_endpoint_is_subscribed
from state import hub
from subscriptions import (
    _response_identity_from_ws_message,
    _summarize_websocket_message,
)

router = APIRouter()


@router.websocket("/bind/{endpoint}")
async def websocket_endpoint(websocket: WebSocket, endpoint: str):
    """WebSocket endpoint for event delivery"""
    if HUB_REQUIRE_AUTH and not _bind_endpoint_is_subscribed(endpoint):
        await websocket.close(code=1008)
        hub.log(
            f"Rejected bind WebSocket for unknown endpoint under HUB_REQUIRE_AUTH: "
            f"{endpoint}"
        )
        return

    await websocket.accept()
    hub.log(f"WebSocket connection accepted for endpoint: {endpoint}")
    
    # Register WebSocket connection
    hub.register_websocket(endpoint, websocket)
    
    # Send initial connection confirmation
    try:
        await websocket.send_json({
            "type": "connection.established",
            "endpoint": endpoint,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        hub.log(f"Error sending connection confirmation: {e}")
    
    # Send admin refresh on connection
    await hub.send_admin_refresh_command()
    
    keepalive_task = None
    if HUB_WS_KEEPALIVE:
        interval = max(1.0, HUB_WS_KEEPALIVE_INTERVAL_SECONDS)

        async def keepalive():
            while True:
                try:
                    await asyncio.sleep(interval)
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": datetime.now().isoformat(),
                    })
                except Exception as e:
                    hub.log(f"Keepalive error for {endpoint}: {e}")
                    break

        keepalive_task = asyncio.create_task(keepalive())

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                msg_type = (
                    message.get("type") if isinstance(message, dict) else None
                )
                if msg_type in ("ping", "pong"):
                    if HUB_WS_KEEPALIVE:
                        summary = _summarize_websocket_message(message)
                        hub.log(
                            f"WebSocket {endpoint}: {summary}",
                            level=logging.DEBUG,
                        )
                    continue

                summary = _summarize_websocket_message(message)
                hub.log(f"WebSocket {endpoint}: {summary}")
                
                # Check if this is a response to a pending request (in event format).
                # hub.event values ending with ``-response`` are treated as Hub responses.
                event = message.get("event", {}) if isinstance(message, dict) else {}
                hub_event_name = event.get("hub.event") if isinstance(event, dict) else None
                if isinstance(hub_event_name, str) and is_response_event(hub_event_name):
                    context = event.get("context", {}) if isinstance(event, dict) else {}
                    request_id = context.get("id") if isinstance(context, dict) else None
                    record = (
                        hub.pending_requests.get(request_id) if isinstance(request_id, str) else None
                    )
                    if record is None:
                        hub.log(
                            f"Received {hub_event_name} for unknown id {request_id}"
                        )
                    else:
                        envelope_id = (
                            str(message.get("id")).strip() if message.get("id") is not None else ""
                        )
                        async with record["lock"]:
                            if envelope_id and envelope_id in record["seen_envelope_ids"]:
                                hub.log(
                                    f"Ignoring duplicate {hub_event_name} envelope id={envelope_id} "
                                    f"for id {request_id}"
                                )
                            else:
                                if envelope_id:
                                    record["seen_envelope_ids"].add(envelope_id)
                                identity = _response_identity_from_ws_message(
                                    message, endpoint=endpoint
                                )
                                responder_name = identity["subscriber"]
                                actor_text = identity["actor"] or (
                                    record.get("actor") or ""
                                )
                                product_name = (
                                    record["subscriber_products"].get(responder_name)
                                    or None
                                )
                                response_data = (
                                    context.get("data") if isinstance(context, dict) else None
                                )
                                record["responses"].append(
                                    {
                                        "id": envelope_id or None,
                                        "subscriber": responder_name or None,
                                        "actor": actor_text or record.get("actor") or None,
                                        "productName": product_name,
                                        "data": response_data,
                                    }
                                )
                                if responder_name:
                                    record["expected"].discard(responder_name)
                                if not record["expected"]:
                                    record["completion"].set()
                                hub.log(
                                    f"Routed {hub_event_name} for id {request_id} "
                                    f"from subscriber='{responder_name}' "
                                    f"({len(record['responses'])} response(s) so far, "
                                    f"{len(record['expected'])} still expected)"
                                )
            except json.JSONDecodeError:
                preview = data if len(data) <= 120 else data[:120] + "..."
                hub.log(f"WebSocket {endpoint}: non-JSON ({len(data)} bytes): {preview}")
    except WebSocketDisconnect:
        hub.log(f"WebSocket disconnected for endpoint: {endpoint}")
    except asyncio.CancelledError:
        hub.log(f"WebSocket cancelled (shutdown) for endpoint: {endpoint}")
        raise
    except Exception as e:
        hub.log(f"WebSocket error for endpoint {endpoint}: {type(e).__name__}: {e}")
        # Send admin refresh on error
        await hub.send_admin_refresh_command()
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

        # Unregister WebSocket connection
        await hub.unregister_websocket(endpoint)
        hub.log(f"WebSocket cleanup completed for endpoint: {endpoint}")
        
        # Send admin refresh on disconnect
        await hub.send_admin_refresh_command()
