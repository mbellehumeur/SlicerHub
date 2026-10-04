"""Bridge LiveSync mrson wire messages over Hub scene-update.

When hub-mirror sends ``{ op: subscribe, ... }`` via scene-update, Image Display:
1. Uploads blob chunks as multipart **scene-update** (HubBlobs + context.files[])
2. Publishes **Snapshot** scene-update with the LiveScene document (first paint)
3. Optionally attaches a Hub sink peer for live MRML events (when ModuleServer is up)

applyOps / reconcile are forwarded to the Hub sink peer when available.
Image Display LiveSync path publishes **only** scene-update (no ImagingStudy-open).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from image_display_client_hub import ImageDisplayClientConnection

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

LIVESYNC_CONTEXT_KEY = "livesync"
LIVESYNC_EXPORT_TIMEOUT_SECONDS = 120.0
HUB_BLOBS_EVENT = "HubBlobs"
SNAPSHOT_EVENT = "Snapshot"


def _wire_from_scene_update_context(context: Any) -> Optional[Any]:
    if isinstance(context, list):
        for item in context:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip().lower()
            if key != LIVESYNC_CONTEXT_KEY:
                continue
            return item.get("resource")
        return None
    if isinstance(context, dict):
        if LIVESYNC_CONTEXT_KEY in context:
            return context.get(LIVESYNC_CONTEXT_KEY)
        if "livesync" in context:
            return context.get("livesync")
    return None


def _livesync_scene_update_message(*, topic: str, wire: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event": {
            "hub.topic": topic,
            "hub.event": "scene-update",
            "context": [
                {
                    "key": LIVESYNC_CONTEXT_KEY,
                    "resource": wire,
                }
            ],
        }
    }


async def handle_scene_update(
    connection: "ImageDisplayClientConnection",
    client: Any,
    message: Dict[str, Any],
) -> None:
    """Handle inbound Hub scene-update (LiveSync wire)."""
    event = message.get("event") if isinstance(message.get("event"), dict) else {}
    context = event.get("context")
    wire = _wire_from_scene_update_context(context)
    if not isinstance(wire, dict):
        LOGGER.debug("scene-update ignored: no livesync wire resource")
        return

    op = str(wire.get("op") or "").strip()
    if op == "subscribe":
        await _respond_to_subscribe(connection, client, wire)
        return

    if op in ("applyOps", "reconcile", "getNode"):
        await _forward_op_to_mrson_peer(connection, wire)
        return

    LOGGER.debug("scene-update livesync op ignored: %s", op or "(event)")


async def _forward_op_to_mrson_peer(
    connection: "ImageDisplayClientConnection",
    wire: Dict[str, Any],
) -> None:
    from image_display_mrson_hub_sink import get_or_create_bridge

    bridge = get_or_create_bridge(connection)

    def run() -> None:
        bridge.deliver_op_on_main(wire)

    try:
        await connection.run_on_main_thread(run)
    except Exception as exc:
        LOGGER.warning(
            "livesync forward op=%s failed: %s", wire.get("op"), exc
        )


def _hash_set_from_export(
    scene_doc: Dict[str, Any], blob_parts: List[Tuple[str, bytes]]
) -> Set[str]:
    from image_display_mrson_hub_sink import collect_sha256_hashes

    hashes: Set[str] = set(collect_sha256_hashes(scene_doc))
    for name, _ in blob_parts:
        n = str(name or "").strip()
        if n.lower().endswith(".bin"):
            n = n[:-4]
        if n.startswith("sha256-"):
            hashes.add(n)
    return hashes


async def _publish_hub_blobs_and_snapshot(
    connection: "ImageDisplayClientConnection",
    client: Any,
    topic: str,
) -> Tuple[Set[str], Optional[Dict[str, Any]]]:
    """Export scene, publish HubBlobs multipart, then Snapshot JSON.

    Returns ``(published_hashes, scene_doc)``.
    """
    from live_scene_export import (
        build_livescene_hub_blobs_scene_update_message,
        export_live_scene_document,
    )

    def export_on_main() -> tuple:
        scene_doc, blob_parts = export_live_scene_document()
        msg = build_livescene_hub_blobs_scene_update_message(
            topic=str(topic),
            blob_parts=blob_parts,
        )
        return scene_doc, blob_parts, msg

    try:
        scene_doc, blob_parts, msg = await asyncio.wait_for(
            connection.run_on_main_thread(export_on_main),
            timeout=LIVESYNC_EXPORT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        LOGGER.warning("livesync subscribe: LiveScene export failed: %s", exc)
        return set(), None

    hash_set = _hash_set_from_export(scene_doc, blob_parts)

    if blob_parts:
        try:
            status = await client.publish(msg)
            LOGGER.info(
                "livesync subscribe: scene-update HubBlobs blobs=%d http=%s",
                len(blob_parts),
                status,
            )
        except Exception as exc:
            LOGGER.warning(
                "livesync subscribe: HubBlobs scene-update failed: %s", exc
            )
    else:
        try:
            await client.publish(
                _livesync_scene_update_message(
                    topic=topic,
                    wire={"event": HUB_BLOBS_EVENT, "files": []},
                )
            )
        except Exception as exc:
            LOGGER.warning("livesync empty HubBlobs publish failed: %s", exc)

    try:
        await client.publish(
            _livesync_scene_update_message(
                topic=topic,
                wire={"event": SNAPSHOT_EVENT, "scene": scene_doc},
            )
        )
        LOGGER.info(
            "livesync subscribe: scene-update Snapshot nodes=%d",
            len(scene_doc.get("nodes") or {}),
        )
    except Exception as exc:
        LOGGER.warning("livesync Snapshot publish failed: %s", exc)

    return hash_set, scene_doc


async def _respond_to_subscribe(
    connection: "ImageDisplayClientConnection",
    client: Any,
    wire: Dict[str, Any],
) -> None:
    topic = connection.get_topic() or ""
    if not topic:
        LOGGER.warning("livesync subscribe: no topic")
        return

    types_raw = wire.get("types")
    types: List[str] = []
    if isinstance(types_raw, list):
        types = [str(t).strip() for t in types_raw if str(t).strip()]

    local_bulk_raw = wire.get("localBulk")
    local_bulk: List[str] = []
    if isinstance(local_bulk_raw, list):
        local_bulk = [str(t).strip() for t in local_bulk_raw if str(t).strip()]

    metadata_only = bool(wire.get("metadataOnly"))

    published, _scene = await _publish_hub_blobs_and_snapshot(
        connection, client, topic
    )

    from image_display_mrson_hub_sink import get_or_create_bridge

    bridge = get_or_create_bridge(connection)
    bridge.mark_hashes_published(set(published))

    def subscribe_peer() -> None:
        bridge.subscribe_on_main(types, local_bulk, metadata_only)

    try:
        await connection.run_on_main_thread(subscribe_peer)
        LOGGER.info(
            "livesync subscribe: Hub sink peer subscribed types=%s localBulk=%s",
            types or "*",
            local_bulk,
        )
    except Exception as exc:
        # First paint uses Snapshot; live updates need ModuleServer Hub sink.
        LOGGER.warning("livesync subscribe: Hub sink peer failed: %s", exc)
