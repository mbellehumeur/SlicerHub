"""POST/DELETE/OPTIONS /api/hub."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from hub_client import is_request_event

from binary_publish import _reject_json_binary_bytes_publish
from http_util import (
    _enforce_bearer_auth,
    _enforce_topic_match,
    _parse_request_body,
    _unsubscribe_endpoint_id,
)
from hub_request import _handle_hub_request
from publish import _handle_multipart_publish, _handle_publish_notification
from state import hub

router = APIRouter()


@router.post("/api/hub/")
@router.post("/api/hub")
async def post_hub(request: Request):
    """Handle subscribe/unsubscribe, publish, and typed ``*-request`` fan-out."""
    identity = _enforce_bearer_auth(request)
    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type.lower():
        try:
            # Topic check happens inside after JSON part parse is awkward; enforce
            # via Authorization only here for multipart (publisher topic in body).
            return await _handle_multipart_publish(request)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            await hub.send_admin_refresh_command()

    try:
        request_data = await _parse_request_body(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse request: {e}")
    
    # Also get query parameters
    query_params = dict(request.query_params)
    request_data.update(query_params)
    
    # Add host header for WebSocket URL generation
    request_data["host"] = request.headers.get("host", request.headers.get("Host", ""))
    
    try:
        hub_mode = request_data.get("hub.mode", request_data.get("hub_mode", "")).strip().lower()
        if hub_mode == "subscribe" or hub_mode == "unsubscribe":
            subscription_data = request_data
            if hub_mode == "unsubscribe":
                # Handle unsubscribe
                result = hub.add_subscription(subscription_data)
                removed_subs = result.get("removed_subscriptions") or []
                if removed_subs:
                    await hub.broadcast_subscription_removed(
                        removed_subs, "unsubscribe"
                    )
                return {"status": "unsubscribed", "removed": result.get("removed", 0)}
            # Handle subscribe — oidc: topic must match token
            sub_topic = str(
                subscription_data.get("hub.topic")
                or subscription_data.get("hub_topic")
                or ""
            ).strip()
            _enforce_topic_match(identity, sub_topic)
            result = hub.add_subscription(subscription_data)
            replaced_subs = result.get("replaced_subscriptions") or []
            if replaced_subs:
                await hub.close_websocket_endpoints(
                    [
                        str(sub.get("websocket_endpoint") or "")
                        for sub in replaced_subs
                    ]
                )
                await hub.broadcast_subscription_removed(
                    replaced_subs, "resubscribe"
                )

            # Return appropriate response - 202 Accepted for subscription requests
            if result.get("websocket_url"):
                return JSONResponse(
                    content={"hub.channel.endpoint": result["websocket_url"]},
                    status_code=202
                )
            return JSONResponse(
                content={"status": "subscribed", "subscription": result["subscription"]},
                status_code=202
            )

        # Publish or typed request via /api/hub or /api/hub/
        event = request_data.get("event")
        if isinstance(event, dict):
            pub_topic = str(event.get("hub.topic") or "").strip()
            _enforce_topic_match(identity, pub_topic)
            hub_event_name = event.get("hub.event")
            if isinstance(hub_event_name, str) and is_request_event(hub_event_name):
                return await _handle_hub_request(request_data)
            _reject_json_binary_bytes_publish(request_data)
            return await _handle_publish_notification(request_data)

        raise HTTPException(
            status_code=400,
            detail="Invalid /api/hub POST payload: expected hub.mode for subscribe/unsubscribe or event payload for publish/request"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        # Send admin refresh command (rate limited)
        await hub.send_admin_refresh_command()


@router.delete("/api/hub/")
@router.delete("/api/hub")
async def delete_hub(request: Request):
    """Handle DELETE /api/hub/ — unsubscribe a specific channel (no mass clear)."""
    _enforce_bearer_auth(request)
    try:
        unsubscribe_data = await _parse_request_body(request)
    except Exception:
        unsubscribe_data = {}

    endpoint = (
        unsubscribe_data.get("hub.channel.endpoint")
        or unsubscribe_data.get("hub_channel_endpoint")
    )
    callback = unsubscribe_data.get("hub.callback") or unsubscribe_data.get(
        "hub_callback"
    )
    topic = unsubscribe_data.get("hub.topic") or unsubscribe_data.get("hub_topic")
    endpoint_id = _unsubscribe_endpoint_id(endpoint)

    if not endpoint_id and not (callback and topic):
        raise HTTPException(
            status_code=400,
            detail=(
                "DELETE /api/hub requires hub.channel.endpoint and/or "
                "hub.callback+hub.topic; mass clear is not supported "
                "(use POST /api/admin/reset)"
            ),
        )

    removed_count, removed_subs = hub.remove_subscription(
        endpoint=endpoint_id,
        callback=callback,
        topic=topic,
    )
    if removed_subs:
        await hub.broadcast_subscription_removed(removed_subs, "unsubscribe")
    await hub.send_admin_refresh_command()
    return {"status": "unsubscribed", "removed": removed_count}

@router.options("/api/hub/")
@router.options("/api/hub")
async def options_hub():
    """Handle CORS preflight requests"""
    return Response(status_code=204)
