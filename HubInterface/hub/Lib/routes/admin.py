"""Admin HTML/JSON APIs and /ws/admin."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from hub_metrics import collect_hub_metrics

from http_util import (
    _enforce_privileged_admin,
    _enforce_privileged_admin_ws,
    _parse_request_body,
    _serve_html_page,
    _subscription_for_admin,
)
from payload_store import _http_payload_store
from state import hub
from subscriptions import _summarize_websocket_message

router = APIRouter()

_HUB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@router.get("/api/hub/admin")
@router.get("/api/hub/admin/")
async def get_hub_status():
    """Get hub status page showing all users and endpoints"""
    return _serve_html_page("admin.html")


def _read_hub_extension_version() -> str:
    """Extension/hub version for admin About (hub/VERSION or parent HubInterface/VERSION)."""
    for candidate in (
        os.path.join(_HUB_DIR, "VERSION"),
        os.path.join(os.path.dirname(_HUB_DIR), "VERSION"),
    ):
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as handle:
                line = handle.readline().strip()
            if line:
                return line
        except OSError:
            pass
    return "development"


@router.get("/api/hub/admin/about")
@router.get("/api/hub/admin/about/")
async def get_admin_about():
    """Metadata for the admin About dialog (extension name, version, homepage)."""
    return {
        "name": "Slicer Hub",
        "description": (
            "3D Slicer extension for desktop integration workflows "
            "for healthcare providers and researchers."
        ),
        "version": _read_hub_extension_version(),
        "homepage": "https://github.com/mbellehumeur/SlicerHub/",
    }


@router.get("/api/hub/admin/metrics")
async def get_admin_metrics(request: Request):
    """Lightweight process metrics for admin sparkline charts."""
    _enforce_privileged_admin(request)
    return collect_hub_metrics(hub, _http_payload_store)


@router.get("/api/hub/admin/snapshot")
async def get_admin_snapshot(
    request: Request,
    publisher: Optional[str] = None,
    topic: Optional[str] = None,
    event: Optional[str] = None,
):
    """Consolidated admin dashboard payload (subscriptions, conferences, audit log)."""
    _enforce_privileged_admin(request)
    subscriptions = hub.get_subscriptions()
    public_subs = [_subscription_for_admin(sub) for sub in subscriptions]
    log_entries = hub.get_audit_log(
        publisher_filter=publisher, topic_filter=topic, event_filter=event
    )
    unique_values = hub.get_audit_log_unique_values()
    conferences = list(hub.conferences)

    authentications = hub.authentications_for_admin()
    return {
        "revision": hub.admin_revision,
        "single_user_mode": hub.single_user_mode,
        "stats": {
            "total_subscriptions": len(subscriptions),
            "total_authentications": len(authentications),
            "total_topics": len(
                set(sub.get("topic") for sub in subscriptions if sub.get("topic"))
            ),
            "total_messages": len(hub.audit_log),
            "total_conferences": len(conferences),
            "total_admin_clients": len(hub.admin_websockets),
        },
        "subscriptions": public_subs,
        "conferences": conferences,
        "authentications": authentications,
        "audit": {
            "entries": log_entries,
            "count": len(log_entries),
            "unique_publishers": unique_values["publishers"],
            "unique_topics": unique_values["topics"],
            "unique_events": unique_values["events"],
        },
    }


@router.post("/api/hub/admin/clear-audit-log")
@router.post("/api/hub/admin/clear-audit-log/")
async def clear_audit_log(request: Request):
    """Clear message log entries only (subscriptions and conferences unchanged)."""
    _enforce_privileged_admin(request)
    hub.audit_log.clear()
    hub.audit_log_counter = 0
    await hub.send_admin_refresh_command()
    return {"ok": True, "total_messages": 0}


@router.post("/api/admin/reset")
async def reset_hub(request: Request):
    """Reset the hub - clear all subscriptions, conferences, and audit log (like restarting the service)"""
    _enforce_privileged_admin(request)
    single_user_mode = False
    try:
        data = await _parse_request_body(request)
        single_user_mode = data.get("single_user_mode", False)
        if isinstance(single_user_mode, str):
            single_user_mode = single_user_mode.lower() == "true"
    except Exception:
        pass

    hub.single_user_mode = single_user_mode

    await hub.reset_all()

    mode_msg = " (Single-user mode enabled)" if single_user_mode else ""
    return {
        "status": "reset",
        "message": (
            "All subscriptions, conferences, audit log cleared, "
            f"and all WebSocket connections disconnected{mode_msg}"
        ),
    }

@router.websocket("/ws/admin")
async def admin_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for admin page - receives refresh commands"""
    if not await _enforce_privileged_admin_ws(websocket):
        hub.log("Rejected admin WebSocket: missing or invalid credentials")
        return

    await websocket.accept()
    
    # Extract location information from request headers
    location = "unknown"
    try:
        # Try to get client IP and other location info
        client_host = websocket.client.host if websocket.client else "unknown"
        client_port = websocket.client.port if websocket.client else "unknown"
        location = f"{client_host}:{client_port}"
    except Exception as e:
        hub.log(f"Could not extract location info: {e}", level=logging.DEBUG)
        location = "unknown"
    
    hub.log(
        f"Admin WebSocket connection accepted from {location}",
        level=logging.DEBUG,
    )
    
    # Register admin WebSocket with location
    hub.register_admin_websocket(websocket, location)
    
    # Send initial connection confirmation
    try:
        await websocket.send_json({
            "type": "connection.established",
            "role": "admin",
            "revision": hub.admin_revision,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        hub.log(
            f"Error sending admin connection confirmation: {e}",
            level=logging.WARNING,
        )
    
    try:
        while True:
            # Receive messages from admin client (pong, etc.)
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                summary = _summarize_websocket_message(message)
                hub.log(
                    f"Admin WebSocket {location}: {summary}",
                    level=logging.DEBUG,
                )
            except json.JSONDecodeError:
                preview = data if len(data) <= 120 else data[:120] + "..."
                hub.log(
                    f"Admin WebSocket {location}: non-JSON ({len(data)} bytes): {preview}",
                    level=logging.DEBUG,
                )
    except WebSocketDisconnect:
        hub.log("Admin WebSocket disconnected", level=logging.DEBUG)
    except asyncio.CancelledError:
        hub.log("Admin WebSocket cancelled (shutdown)", level=logging.DEBUG)
        raise
    except Exception as e:
        hub.log(
            f"Admin WebSocket error: {type(e).__name__}: {e}",
            level=logging.WARNING,
        )
    finally:
        # Unregister admin WebSocket
        hub.unregister_admin_websocket(websocket)
        hub.log("Admin WebSocket cleanup completed", level=logging.DEBUG)
