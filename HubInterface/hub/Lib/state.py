"""Hub singleton state (subscriptions, websockets, audit, conferences)."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, WebSocket
from hub_client import SUBSCRIPTION_REMOVED

from audit_helpers import _audit_publisher_name
from logging_setup import hub_logger
from subscriptions import (
    _subscription_actor_list,
    _subscription_log_message,
    _subscription_product_name,
)

# Hub-generated lifecycle event (not FHIRcast catalog); fan-out to peers on same topic.
SUBSCRIPTION_REMOVED_EVENT = SUBSCRIPTION_REMOVED


class Hub:
    """Slicer Hub implementation for managing subscriptions and broadcasting events"""
    
    def __init__(self):
        self.subscriptions: List[Dict] = []
        self.websocket_connections: Dict[str, WebSocket] = {}  # endpoint -> WebSocket
        self.admin_websockets: List[Dict] = []  # Track admin connections with metadata: [{"websocket": WebSocket, "location": str, "connected_at": str}]
        self.conferences: List[Dict] = []
        self.audit_log: List[Dict] = []  # List of logged events
        self.audit_log_counter: int = 0  # Incrementing message number
        self.server_port = 2018
        self.user_count = 0
        self.last_admin_refresh_time: float = 0.0  # Track last admin refresh time for rate limiting
        self.pending_admin_refresh_task = None  # Track pending refresh task (to cancel if needed)
        self.admin_revision: int = 0  # Bumped when admin clients are notified of hub changes
        self.single_user_mode: bool = False  # When enabled, token endpoint always returns topic 'SINGLE-USER'
        # request_id -> { actor, product_name, expected, subscriber_products,
        #                  responses, seen_envelope_ids, event_name,
        #                  completion: asyncio.Event, lock: asyncio.Lock,
        #                  data_type, started_at, request_topic }
        self.pending_requests: Dict[str, Dict[str, Any]] = {}
        # OIDC login PKCE state: state -> { code_verifier, return_to, expires_at }
        self.oidc_login_states: Dict[str, Dict[str, Any]] = {}
        # One entry per authenticated user (keyed by topic):
        # { user_name, topic, authenticated_at, timezone, language, platform }
        self.authentications: Dict[str, Dict[str, Any]] = {}

    def log(self, message: str, level: int = logging.INFO) -> None:
        """Log hub diagnostics via the hub logger."""
        hub_logger.log(level, message)

    def upsert_authentication(self, user_name: str, topic: str) -> Dict[str, Any]:
        """Record or refresh one authentication row per user/topic."""
        topic_key = str(topic or "").strip()
        name = str(user_name or "").strip()
        if not topic_key:
            topic_key = name
        if not topic_key:
            return {}
        existing = self.authentications.get(topic_key)
        if isinstance(existing, dict):
            existing["user_name"] = name or existing.get("user_name") or topic_key
            existing["topic"] = topic_key
            existing["authenticated_at"] = datetime.now().isoformat()
            return existing
        entry = {
            "user_name": name or topic_key,
            "topic": topic_key,
            "authenticated_at": datetime.now().isoformat(),
            "timezone": "",
            "language": "",
            "platform": "",
        }
        self.authentications[topic_key] = entry
        return entry

    def enrich_authentication_from_client_info(
        self, topic: str, client_info: Optional[Dict[str, Any]]
    ) -> None:
        """Update timezone/language/platform on an auth row from subscribe client_info."""
        topic_key = str(topic or "").strip()
        if not topic_key:
            return
        entry = self.authentications.get(topic_key)
        if not isinstance(entry, dict):
            return
        if not isinstance(client_info, dict):
            return
        for key in ("timezone", "language", "platform"):
            value = client_info.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                entry[key] = value_str

    def authentications_for_admin(self) -> List[Dict[str, Any]]:
        """Sorted public authentication rows for the admin snapshot."""
        rows = list(self.authentications.values())
        rows.sort(
            key=lambda row: str(row.get("user_name") or row.get("topic") or "").lower()
        )
        return [
            {
                "user_name": str(row.get("user_name") or ""),
                "topic": str(row.get("topic") or ""),
                "authenticated_at": str(row.get("authenticated_at") or ""),
                "timezone": str(row.get("timezone") or ""),
                "language": str(row.get("language") or ""),
                "platform": str(row.get("platform") or ""),
            }
            for row in rows
            if isinstance(row, dict)
        ]

    def set_server_port(self, port: int):
        """Set the server port for generating WebSocket URLs"""
        self.server_port = port
    
    def check_subscription_request(self, subscription_request: Dict) -> Dict:
        """Verify WebSub subscription callback"""
        callback = subscription_request.get("hub.callback") or subscription_request.get("hub_callback")
        secret = subscription_request.get("hub.secret") or subscription_request.get("hub_secret")
        topic = subscription_request.get("hub.topic") or subscription_request.get("hub_topic")
        
        if not callback or not secret or not topic:
            return {"status": 400, "data": "Missing required parameters"}
        
        try:
            challenge_url = f"{callback}?hub.challenge={secret}&hub.topic={topic}"
            req = urllib.request.Request(challenge_url)
            with urllib.request.urlopen(req, timeout=5) as response:
                status = response.getcode()
                data = response.read().decode()
                if data == secret and status == 200:
                    return {"status": 200, "data": data}
                else:
                    return {"status": 500, "data": "Verification failed"}
        except Exception as e:
            self.log(f"WebSub verification error: {e}")
            return {"status": 500, "data": str(e)}
    
    def add_subscription(self, subscription_data: Dict) -> Dict:
        """Handle subscription/unsubscription requests - matches HubRequestHandler.handleHubSubscription"""
        hub_mode = subscription_data.get("hub.mode", subscription_data.get("hub_mode", "subscribe"))
        hub_topic = subscription_data.get("hub.topic", subscription_data.get("hub_topic", ""))
        hub_events = subscription_data.get("hub.events", subscription_data.get("hub_events", ""))
        hub_callback = subscription_data.get("hub.callback", subscription_data.get("hub_callback", ""))
        hub_secret = subscription_data.get("hub.secret", subscription_data.get("hub_secret", ""))
        hub_lease = subscription_data.get("hub.lease_seconds", subscription_data.get("hub.lease", subscription_data.get("hub_lease", "7200")))
        subscriber_name = subscription_data.get("subscriber.name", subscription_data.get("subscriber_name", "unknown"))
        subscriber_product = subscription_data.get("subscriber.product.name")
        subscriber_version = subscription_data.get("subscriber.product.version")
        raw_sub_actors = subscription_data.get("subscriber.actors")
        raw_client_info = subscription_data.get("subscriber.client_info")
        subscriber_actors = []
        if raw_sub_actors is not None:
            try:
                parsed_sub_actors = (
                    json.loads(raw_sub_actors)
                    if isinstance(raw_sub_actors, str)
                    else raw_sub_actors
                )
                if isinstance(parsed_sub_actors, list):
                    subscriber_actors = [
                        str(x).strip() for x in parsed_sub_actors if str(x).strip()
                    ]
                elif isinstance(parsed_sub_actors, str):
                    s = parsed_sub_actors.strip()
                    if s:
                        subscriber_actors = [s]
            except (json.JSONDecodeError, TypeError):
                s = str(raw_sub_actors).strip()
                if s:
                    subscriber_actors = [s]
        client_info = None
        if raw_client_info is not None:
            try:
                parsed_client_info = (
                    json.loads(raw_client_info)
                    if isinstance(raw_client_info, str)
                    else raw_client_info
                )
                if isinstance(parsed_client_info, dict):
                    cleaned_client_info = {}
                    for key in (
                        "productName",
                        "version",
                        "userAgent",
                        "platform",
                        "language",
                        "timezone",
                    ):
                        value = parsed_client_info.get(key)
                        if value is not None:
                            value_str = str(value).strip()
                            if value_str:
                                cleaned_client_info[key] = value_str
                    if cleaned_client_info:
                        client_info = cleaned_client_info
            except (json.JSONDecodeError, TypeError, ValueError):
                # Optional metadata: ignore malformed values silently.
                client_info = None
        subscriber_product = str(subscriber_product).strip() if subscriber_product is not None else ""
        subscriber_version = str(subscriber_version).strip() if subscriber_version is not None else ""
        if subscriber_product or subscriber_version:
            if not client_info:
                client_info = {}
            # Prefer explicit subscriber.product.name/version over optional client_info.
            if subscriber_product:
                client_info["productName"] = subscriber_product
            if subscriber_version:
                client_info["version"] = subscriber_version
        channel_type = subscription_data.get("hub.channel.type", subscription_data.get("hub_channel_type", "websub"))
        channel_endpoint = subscription_data.get("hub.channel.endpoint", subscription_data.get("hub_channel_endpoint", ""))
        host = subscription_data.get("host", subscription_data.get("Host", ""))

        if hub_mode == "subscribe":
            # Verify subscription request for WebSub
            if channel_type != "websocket":
                verify_result = self.check_subscription_request({
                    "hub.callback": hub_callback,
                    "hub.secret": hub_secret,
                    "hub.topic": hub_topic
                })
                
                if verify_result["status"] != 200:
                    self.log(f"WebSub verification failed: {verify_result['status']}")
                    raise ValueError("WebSub verification failed")
            
            # Generate WebSocket endpoint identifier
            websocket_endpoint = str(uuid.uuid4())
            
            # Determine protocol and host from request
            # Use wss for HTTPS (Azure), ws for HTTP (local)
            # Extract host from request headers or use provided host
            request_host = host if host else f"localhost:{self.server_port}"
            
            # Determine if HTTPS based on common patterns
            is_secure = (
                "azurewebsites.net" in request_host or 
                "https" in request_host or
                request_host.startswith("secure.") or
                not "localhost" in request_host.lower()
            )
            protocol = "wss" if is_secure else "ws"
            
            websocket_url = f"{protocol}://{request_host}/bind/{websocket_endpoint}"
            
            replaced_subscriptions: List[Dict] = []
            if channel_type == "websocket":
                replaced_subscriptions = (
                    self._take_matching_websocket_subscriptions(
                        subscriber_name, hub_topic
                    )
                )

            subscription = {
                "channel": channel_type,
                "endpoint": websocket_url if channel_type == "websocket" else hub_callback,
                "websocket_endpoint": websocket_endpoint,
                "callback": hub_callback,
                "events": hub_events,
                "secret": hub_secret,
                "topic": hub_topic,
                "lease": int(hub_lease),
                "session": hub_topic,
                "subscriber": subscriber_name,
                "actors": subscriber_actors,
                "client_info": client_info,
                "host": host,
                "created": datetime.now().isoformat()
            }
            
            self.subscriptions.append(subscription)
            self.enrich_authentication_from_client_info(hub_topic, client_info)
            self.log(
                _subscription_log_message(
                    "added",
                    subscriber_name=subscriber_name,
                    hub_topic=hub_topic,
                    channel_type=channel_type,
                    hub_events=hub_events,
                    subscriber_actors=subscriber_actors,
                    subscriber_product=subscriber_product,
                    subscriber_version=subscriber_version,
                    websocket_endpoint=websocket_endpoint,
                )
            )

            return {
                "subscription": subscription,
                "websocket_url": websocket_url if channel_type == "websocket" else None,
                "replaced_subscriptions": replaced_subscriptions,
            }
        
        elif hub_mode == "unsubscribe":
            # Handle unsubscribe
            endpoint_id = (
                channel_endpoint.split("/bind/")[-1]
                if channel_endpoint and "/bind/" in channel_endpoint
                else None
            )
            removed_count, removed_subs = self.remove_subscription(
                endpoint=endpoint_id,
                callback=hub_callback,
                topic=hub_topic,
            )
            if removed_count == 0:
                self.log(
                    _subscription_log_message(
                        "unsubscribe (no match)",
                        subscriber_name=subscriber_name,
                        hub_topic=hub_topic,
                        channel_type=channel_type,
                        hub_events=hub_events,
                        subscriber_actors=subscriber_actors,
                        subscriber_product=subscriber_product,
                        subscriber_version=subscriber_version,
                        websocket_endpoint=endpoint_id or "",
                    )
                )
            return {
                "removed": removed_count,
                "removed_subscriptions": removed_subs,
            }
        
        else:
            raise ValueError(f"Invalid hub.mode: {hub_mode}")
    
    def _copy_subscription_record(self, sub: Dict) -> Dict:
        return copy.deepcopy(sub)

    def _take_matching_websocket_subscriptions(
        self, subscriber: str, topic: str
    ) -> List[Dict]:
        """Remove websocket rows for the same subscriber+topic (resubscribe replace).

        Matching is case-insensitive on ``subscriber`` and exact on ``topic``.
        Returns deep copies of removed records for fan-out. Does not close bind
        sockets; the caller must close endpoints separately.
        """
        subscriber_key = (subscriber or "").strip().lower()
        topic_key = topic or ""
        if not subscriber_key:
            return []

        taken: List[Dict] = []
        for sub in self.subscriptions[:]:
            if sub.get("channel") != "websocket":
                continue
            if sub.get("topic") != topic_key:
                continue
            if str(sub.get("subscriber") or "").strip().lower() != subscriber_key:
                continue
            client_info = (
                sub.get("client_info")
                if isinstance(sub.get("client_info"), dict)
                else {}
            )
            self.log(
                _subscription_log_message(
                    "replaced",
                    subscriber_name=str(sub.get("subscriber") or "unknown"),
                    hub_topic=str(sub.get("topic") or ""),
                    channel_type=str(sub.get("channel") or ""),
                    hub_events=sub.get("events"),
                    subscriber_actors=sub.get("actors") or [],
                    subscriber_product=str(client_info.get("productName") or ""),
                    subscriber_version=str(client_info.get("version") or ""),
                    websocket_endpoint=str(sub.get("websocket_endpoint") or ""),
                )
            )
            taken.append(self._copy_subscription_record(sub))
            self.subscriptions.remove(sub)

        if taken:
            self.log(
                f"Subscription replace: removed={len(taken)} "
                f"remaining={len(self.subscriptions)} "
                f"subscriber={subscriber!r} topic={topic_key!r}"
            )
        return taken

    async def close_websocket_endpoints(self, endpoints: List[str]) -> None:
        """Pop and close bind sockets without touching the subscription list."""
        for endpoint in endpoints:
            if not endpoint:
                continue
            websocket = self.websocket_connections.pop(endpoint, None)
            if websocket is None:
                continue
            try:
                await websocket.close()
                self.log(f"WebSocket closed for replaced endpoint: {endpoint}")
            except Exception as e:
                self.log(
                    f"Error closing replaced WebSocket {endpoint}: {e}"
                )

    async def broadcast_subscription_removed(
        self, removed_subs: List[Dict], reason: str
    ) -> None:
        """Notify other subscribers that a peer subscription was removed."""
        for sub in removed_subs:
            topic = str(sub.get("topic") or "").strip()
            if not topic:
                continue
            subscriber_name = str(sub.get("subscriber") or "").strip()
            product = _subscription_product_name(sub)
            actors = _subscription_actor_list(sub)
            notification = {
                "timestamp": datetime.now().isoformat(),
                "id": str(uuid.uuid4()),
                "subscriber.name": subscriber_name,
                "event": {
                    "hub.topic": topic,
                    "hub.event": SUBSCRIPTION_REMOVED_EVENT,
                    "context": {
                        "reason": reason,
                        "subscriber": subscriber_name,
                    },
                },
            }
            if product:
                notification["subscriber.product.name"] = product
            if len(actors) == 1:
                notification["subscriber.actor"] = actors[0]
            elif len(actors) > 1:
                notification["subscriber.actor"] = actors
            try:
                from publish import _handle_publish_notification

                await _handle_publish_notification(notification)
            except HTTPException as exc:
                self.log(
                    f"subscription-removed fan-out failed for topic '{topic}': "
                    f"{exc.detail}"
                )
            except Exception as exc:
                self.log(
                    f"subscription-removed fan-out error for topic '{topic}': "
                    f"{type(exc).__name__}: {exc}"
                )

    def remove_subscription(
        self,
        endpoint: str = None,
        callback: str = None,
        topic: str = None,
    ) -> Tuple[int, List[Dict]]:
        """Remove subscriptions matching the given criteria.

        Returns ``(removed_count, copies_of_removed_records)`` for fan-out.
        """
        removed_count = 0
        removed_subs: List[Dict] = []
        for sub in self.subscriptions[:]:
            matched = False
            if endpoint and sub.get("websocket_endpoint") == endpoint:
                matched = True
            elif (
                callback
                and sub.get("callback") == callback
                and topic
                and sub.get("topic") == topic
            ):
                matched = True
            if not matched:
                continue
            client_info = sub.get("client_info") if isinstance(sub.get("client_info"), dict) else {}
            self.log(
                _subscription_log_message(
                    "removed",
                    subscriber_name=str(sub.get("subscriber") or "unknown"),
                    hub_topic=str(sub.get("topic") or ""),
                    channel_type=str(sub.get("channel") or ""),
                    hub_events=sub.get("events"),
                    subscriber_actors=sub.get("actors") or [],
                    subscriber_product=str(client_info.get("productName") or ""),
                    subscriber_version=str(client_info.get("version") or ""),
                    websocket_endpoint=str(sub.get("websocket_endpoint") or ""),
                )
            )
            removed_subs.append(self._copy_subscription_record(sub))
            self.subscriptions.remove(sub)
            removed_count += 1

        if removed_count > 0:
            self.log(
                f"Subscription cleanup: removed={removed_count} "
                f"remaining={len(self.subscriptions)}"
            )
        return removed_count, removed_subs
    
    def get_subscriptions(self) -> List[Dict]:
        """Get all active subscriptions"""
        return self.subscriptions
    
    def send_event(self, topic: str, event_type: str, event_data: Dict):
        """Helper method to create a notification (actual sending happens in async endpoint)"""
        notification = {
            "timestamp": datetime.now().isoformat(),
            "id": str(uuid.uuid4()),
            "event": {
                "hub.topic": topic,
                "hub.event": event_type,
                "context": event_data
            }
        }
        return notification
    
    def register_websocket(self, endpoint: str, websocket: WebSocket):
        """Register a WebSocket connection"""
        self.websocket_connections[endpoint] = websocket
        self.log(f"WebSocket registered for endpoint: {endpoint} (total: {len(self.websocket_connections)})")
    
    async def unregister_websocket(self, endpoint: str):
        """Unregister a WebSocket connection and notify peers."""
        if endpoint in self.websocket_connections:
            del self.websocket_connections[endpoint]
        _removed_count, removed_subs = self.remove_subscription(endpoint=endpoint)
        if removed_subs:
            await self.broadcast_subscription_removed(
                removed_subs, "websocket-disconnect"
            )
        self.log(
            f"WebSocket unregistered for endpoint: {endpoint} "
            f"(remaining: {len(self.websocket_connections)})"
        )
    
    def register_admin_websocket(self, websocket: WebSocket, location: str = "unknown"):
        """Register an admin WebSocket connection with location info"""
        # Check if this websocket is already registered
        for admin_client in self.admin_websockets:
            if admin_client["websocket"] == websocket:
                return  # Already registered
        
        admin_client = {
            "websocket": websocket,
            "location": location,
            "connected_at": datetime.now().isoformat()
        }
        self.admin_websockets.append(admin_client)
        self.log(
            f"Admin WebSocket registered from {location} (total: {len(self.admin_websockets)})",
            level=logging.DEBUG,
        )
    
    def unregister_admin_websocket(self, websocket: WebSocket):
        """Unregister an admin WebSocket connection"""
        for admin_client in self.admin_websockets[:]:
            if admin_client["websocket"] == websocket:
                location = admin_client.get("location", "unknown")
                self.admin_websockets.remove(admin_client)
                self.log(
                    f"Admin WebSocket unregistered from {location} (remaining: {len(self.admin_websockets)})",
                    level=logging.DEBUG,
                )
                return
    
    async def _do_send_admin_refresh(self):
        """Internal method to actually send the refresh command"""
        if not self.admin_websockets:
            return
        
        self.last_admin_refresh_time = time.time()
        self.admin_revision += 1

        message = {
            "type": "admin.refresh",
            "revision": self.admin_revision,
            "timestamp": datetime.now().isoformat(),
        }
        
        disconnected = []
        for admin_client in self.admin_websockets:
            try:
                await admin_client["websocket"].send_json(message)
            except Exception as e:
                self.log(f"Error sending refresh to admin: {e}")
                disconnected.append(admin_client["websocket"])
        
        # Clean up disconnected websockets
        for ws in disconnected:
            self.unregister_admin_websocket(ws)
    
    async def send_admin_refresh_command(self):
        """Send refresh command to all connected admin clients (rate limited to max 1 per 2 seconds)
        If rate limited, schedules a delayed send. Only the last suppressed refresh will be sent.
        """
        if not self.admin_websockets:
            return
        
        current_time = time.time()
        time_since_last = current_time - self.last_admin_refresh_time
        
        # If enough time has passed (>= 2 seconds), send immediately
        if time_since_last >= 2.0:
            # Cancel any pending task since we're sending now
            if self.pending_admin_refresh_task and not self.pending_admin_refresh_task.done():
                self.pending_admin_refresh_task.cancel()
                self.pending_admin_refresh_task = None
            
            await self._do_send_admin_refresh()
        else:
            # Rate limited - cancel any existing pending task and schedule a new one
            # This ensures only the last suppressed refresh is sent
            if self.pending_admin_refresh_task and not self.pending_admin_refresh_task.done():
                self.pending_admin_refresh_task.cancel()
            
            # Schedule to send after remaining time (2.0 - time_since_last)
            delay = 2.0 - time_since_last
            
            async def delayed_send():
                await asyncio.sleep(delay)
                await self._do_send_admin_refresh()
                self.pending_admin_refresh_task = None
            
            self.pending_admin_refresh_task = asyncio.create_task(delayed_send())

    async def cancel_pending_admin_refresh(self) -> None:
        """Cancel a scheduled admin refresh (e.g. before hub reset)."""
        task = self.pending_admin_refresh_task
        if task is None or task.done():
            self.pending_admin_refresh_task = None
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.pending_admin_refresh_task = None
    
    def add_audit_log(
        self,
        subscriber: str,
        topic: str,
        event_name: str,
        event_data: Dict,
        direction: str = "received",
        publisher: Optional[Dict[str, Optional[str]]] = None,
        message_params: Optional[Dict[str, Any]] = None,
    ):
        """Add an entry to the audit log.

        ``subscriber`` identifies the subscription row (delivery target for
        ``direction='sent'``, or the requester for hub-request rows).
        ``publisher`` describes who published the event (name, product).
        ``message_params`` stores Hub wire parameters (no context payload) for admin UI.
        """
        self.audit_log_counter += 1
        log_entry: Dict[str, Any] = {
            "message_number": self.audit_log_counter,
            "timestamp": datetime.now().isoformat(),
            "subscriber": (subscriber or "").strip() or "unknown",
            "topic": topic,
            "event_name": event_name,
            "event_data": event_data,
            "direction": direction,
        }
        if publisher:
            log_entry["publisher"] = publisher
        if message_params:
            log_entry["message_params"] = message_params
        self.audit_log.append(log_entry)
        if len(self.audit_log) > 1000:
            self.audit_log = self.audit_log[-1000:]

    def get_audit_log(
        self,
        publisher_filter: Optional[str] = None,
        topic_filter: Optional[str] = None,
        event_filter: Optional[str] = None,
    ) -> List[Dict]:
        """Return audit log entries, optionally filtered by publisher, topic, or event."""
        filtered_log = self.audit_log
        if publisher_filter:
            key = publisher_filter.lower()
            filtered_log = [
                entry
                for entry in filtered_log
                if key in _audit_publisher_name(entry).lower()
            ]
        if topic_filter:
            filtered_log = [
                entry
                for entry in filtered_log
                if topic_filter.lower() in entry.get("topic", "").lower()
            ]
        if event_filter:
            filtered_log = [
                entry
                for entry in filtered_log
                if event_filter.lower() in entry.get("event_name", "").lower()
            ]
        return list(reversed(filtered_log))

    def get_audit_log_unique_values(self) -> Dict[str, List[str]]:
        """Unique publishers, topics, and events from the audit log."""
        publishers: Set[str] = set()
        topics: Set[str] = set()
        events: Set[str] = set()
        for entry in self.audit_log:
            name = _audit_publisher_name(entry)
            if name:
                publishers.add(name)
            topic = entry.get("topic")
            event_name = entry.get("event_name")
            if topic and str(topic).strip():
                topics.add(str(topic).strip())
            if event_name and str(event_name).strip():
                events.add(str(event_name).strip())
        return {
            "publishers": sorted(publishers),
            "topics": sorted(topics),
            "events": sorted(events),
        }

    async def reset_all(self):
        """Reset everything - clear subscriptions, conferences, and audit log (like restarting the service)"""
        await self.cancel_pending_admin_refresh()

        for record in self.pending_requests.values():
            completion = record.get("completion")
            if completion is not None:
                completion.set()

        disconnected_endpoints = []
        for endpoint, websocket in list(self.websocket_connections.items()):
            try:
                await websocket.close()
                self.log(f"WebSocket closed for endpoint: {endpoint}")
            except Exception as e:
                self.log(f"Error closing WebSocket for endpoint {endpoint}: {e}")
            disconnected_endpoints.append(endpoint)

        for admin_client in list(self.admin_websockets):
            try:
                await admin_client["websocket"].close()
                self.log("Admin WebSocket closed", level=logging.DEBUG)
            except Exception as e:
                self.log(f"Error closing admin WebSocket: {e}", level=logging.WARNING)

        self.subscriptions.clear()
        self.websocket_connections.clear()
        self.admin_websockets.clear()
        self.conferences.clear()
        self.audit_log.clear()
        self.audit_log_counter = 0
        self.user_count = 0
        self.pending_requests.clear()
        self.oidc_login_states.clear()
        self.authentications.clear()
        self.admin_revision = 0
        self.last_admin_refresh_time = 0.0

        self.log(
            f"Hub reset - all subscriptions, conferences, audit log, and authentications "
            f"cleared; "
            f"{len(disconnected_endpoints)} subscriber WebSocket(s) disconnected"
        )


# Global Slicer Hub instance
hub = Hub()
