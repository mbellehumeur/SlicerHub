"""Attach Image Display as an internal ModuleServer mrson Hub sink peer.

Outbound LiveSync events reuse ``_Peer.send`` → ``CastSink.send_text`` → Hub
``scene-update``. SnapshotComplete is dropped on the Hub path only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from image_display_client_hub import ImageDisplayClientConnection

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

HUB_BLOBS_EVENT = "HubBlobs"
_SHA256_RE = re.compile(r"sha256-[a-fA-F0-9]{64}")


def _module_server_live() -> Any:
    try:
        import slicer

        return getattr(slicer, "moduleServerLive", None)
    except Exception:
        return None


def _slicerlive_root_candidates() -> List[str]:
    """Locate SlicerLive repo root (contains ModuleServer/ + LiveStory/)."""
    roots: List[str] = []
    for env_name in ("MODULESERVER_ROOT", "HUB_SLICERLIVE_ROOT"):
        v = (os.environ.get(env_name) or "").strip()
        if v:
            roots.append(os.path.abspath(v))
    try:
        import slicer

        app = getattr(slicer, "app", None)
        home = getattr(app, "slicerHome", None) or "" if app is not None else ""
        if home:
            roots.append(
                os.path.abspath(os.path.join(home, "..", "SlicerLive"))
            )
    except Exception:
        pass
    here = os.path.abspath(os.path.dirname(__file__))
    roots.extend(
        [
            os.path.normpath(
                os.path.join(
                    here, "..", "..", "..", "..", "..", "pw46", "SlicerLive"
                )
            ),
            os.path.normpath(
                os.path.join(here, "..", "..", "..", "..", "pw46", "SlicerLive")
            ),
            os.path.expanduser("~/src/pw46/SlicerLive"),
            os.path.expanduser("~/slicer/SlicerLive"),
        ]
    )
    out: List[str] = []
    seen: Set[str] = set()
    for r in roots:
        r = os.path.abspath(r)
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _ensure_module_server_live(ws_port: int = 2132, http_port: int = 2131) -> Any:
    """Start ModuleServer mrson peer in-process if Hub sink API is missing.

    Mirrors ModuleServer/python/bootstrap.py ``_start_mrson`` so Image Display
    can attach a Hub sink without a separate ModuleServer launch.
    """
    import sys

    import slicer

    live = _module_server_live()
    if live is not None and hasattr(live, "attach_cast_sink"):
        return live

    if live is not None and hasattr(live, "stop"):
        try:
            live.stop()
        except Exception as exc:
            LOGGER.warning("stopping stale moduleServerLive failed: %s", exc)
        try:
            delattr(slicer, "moduleServerLive")
        except Exception:
            slicer.moduleServerLive = None  # type: ignore[attr-defined]

    try:
        ws_port = int(os.environ.get("MODULESERVER_WS_PORT") or ws_port)
    except Exception:
        ws_port = 2132
    try:
        http_port = int(os.environ.get("MODULESERVER_HTTP_PORT") or http_port)
    except Exception:
        http_port = 2131

    root = None
    ms_python = None
    for candidate in _slicerlive_root_candidates():
        py = os.path.join(candidate, "ModuleServer", "python")
        live_story = os.path.join(candidate, "LiveStory")
        if os.path.isdir(py) and os.path.isfile(os.path.join(py, "mrson_peer.py")):
            root = candidate
            ms_python = py
            if os.path.isdir(live_story) and live_story not in sys.path:
                sys.path.insert(0, live_story)
            break
    if not ms_python:
        raise RuntimeError(
            "Cannot auto-start ModuleServer LiveSync peer: SlicerLive root not found "
            "(set MODULESERVER_ROOT or HUB_SLICERLIVE_ROOT)."
        )

    if ms_python not in sys.path:
        sys.path.insert(0, ms_python)

    try:
        from LiveStoryLib import mrson_server as live_mrson_server  # type: ignore
        import mrson_peer  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            f"Cannot import ModuleServer mrson from {root}: {exc}"
        ) from exc

    LOGGER.info(
        "auto-starting ModuleServer LiveSync peer root=%s ws=%s http=%s",
        root,
        ws_port,
        http_port,
    )
    try:
        http = live_mrson_server.startMrsonServer(int(http_port))
        slicer.moduleServerHttp = http  # type: ignore[attr-defined]
    except Exception as exc:
        LOGGER.warning("ModuleServer HTTP auto-start failed (peer ok): %s", exc)
    peer = mrson_peer.startMrsonPeer(int(ws_port))
    slicer.moduleServerLive = peer  # type: ignore[attr-defined]

    if not hasattr(peer, "attach_cast_sink"):
        raise RuntimeError(
            "ModuleServer LiveSync peer started but attach_cast_sink is missing "
            "(update SlicerLive ModuleServer/python/mrson_peer.py)."
        )
    return peer


def collect_sha256_hashes(obj: Any, out: Optional[Set[str]] = None) -> Set[str]:
    """Walk a NodeAdded (or subtree) for content-addressed blob hashes."""
    if out is None:
        out = set()
    if isinstance(obj, str):
        if obj.startswith("sha256-"):
            # bare hash or path ending in hash
            base = obj.rsplit("/", 1)[-1]
            base = base.replace(".bin", "") if base.lower().endswith(".bin") else base
            if _SHA256_RE.fullmatch(base) or (
                base.startswith("sha256-") and len(base) >= 71
            ):
                out.add(base if base.startswith("sha256-") else obj)
            m = _SHA256_RE.search(obj)
            if m:
                out.add(m.group(0))
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            collect_sha256_hashes(v, out)
        return out
    if isinstance(obj, (list, tuple)):
        for v in obj:
            collect_sha256_hashes(v, out)
        return out
    return out


def _blob_bytes_from_live_dir(hash_name: str) -> Optional[bytes]:
    """Read a chunk from ModuleServer ``mrson_live/blobs`` if present."""
    try:
        from LiveStoryLib import mrson_server as HS
    except Exception:
        try:
            import mrson_server as HS  # type: ignore
        except Exception:
            return None
    try:
        blob_dir = os.path.join(HS._live_dir(), "blobs")
    except Exception:
        return None
    for name in (hash_name, f"{hash_name}.bin"):
        path = os.path.join(blob_dir, name)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    return fh.read()
            except Exception:
                return None
    return None


class MrsonHubBridge:
    """Owns the Hub sink peer and publishes outbound wires to the hub."""

    def __init__(self, connection: "ImageDisplayClientConnection") -> None:
        self._connection = connection
        self._peer: Any = None
        self._published_hashes: Set[str] = set()
        self._outbound_lock = asyncio.Lock()
        self._attach_queue: Optional[asyncio.Queue] = None

    @property
    def peer(self) -> Any:
        return self._peer

    def mark_hashes_published(self, hashes: Set[str]) -> None:
        self._published_hashes.update(hashes)

    def ensure_attached(self) -> Any:
        """Attach Hub sink on ModuleServer peer (main/Qt thread). Returns _Peer."""
        live = _ensure_module_server_live()
        # Already attached to this bridge?
        existing = live.cast_peer() if hasattr(live, "cast_peer") else None
        if existing is not None and existing is self._peer:
            return self._peer

        def on_text(text: str) -> None:
            self._on_sink_text(text)

        self._peer = live.attach_cast_sink(on_text)
        LOGGER.info("livesync Hub sink attached to ModuleServer mrson peer")
        return self._peer

    def detach(self) -> None:
        live = _module_server_live()
        if live is not None and hasattr(live, "detach_cast_sink"):
            try:
                live.detach_cast_sink()
            except Exception as exc:
                LOGGER.warning("detach_cast_sink failed: %s", exc)
        self._peer = None
        self._published_hashes.clear()

    def subscribe_on_main(
        self, types: List[str], local_bulk: List[str], metadata_only: bool = False
    ) -> None:
        """Subscribe the Hub sink peer (must run on Slicer main thread)."""
        peer = self.ensure_attached()
        peer.subscribe(types, local_bulk, metadata_only)

    def deliver_op_on_main(self, wire: Dict[str, Any]) -> None:
        """Forward applyOps / reconcile / getNode to the Hub sink peer."""
        peer = self.ensure_attached()
        peer.on_message(wire)

    def _on_sink_text(self, text: str) -> None:
        """Called from Qt/main thread via _Peer.send → CastSink.send_text."""
        try:
            wire = json.loads(text)
        except Exception:
            return
        if not isinstance(wire, dict):
            return
        if str(wire.get("event") or "") == "SnapshotComplete":
            return

        loop = self._connection._loop
        if loop is None:
            LOGGER.warning("livesync Hub sink: hub loop not ready; dropping event")
            return

        try:
            asyncio.run_coroutine_threadsafe(self._publish_outbound(wire), loop)
        except Exception as exc:
            LOGGER.warning("livesync Hub sink schedule failed: %s", exc)

    async def _publish_outbound(self, wire: Dict[str, Any]) -> None:
        async with self._outbound_lock:
            client = self._connection.get_client()
            topic = self._connection.get_topic() or ""
            if client is None or not topic:
                return

            if str(wire.get("event") or "") == "NodeAdded":
                await self._ensure_node_blobs(client, topic, wire)

            try:
                from image_display_livesync_bridge import (
                    _livesync_scene_update_message,
                )

                await client.publish(
                    _livesync_scene_update_message(topic=topic, wire=wire)
                )
            except Exception as exc:
                LOGGER.warning(
                    "livesync Hub sink publish failed event=%s: %s",
                    wire.get("event"),
                    exc,
                )

    async def _ensure_node_blobs(
        self, client: Any, topic: str, wire: Dict[str, Any]
    ) -> None:
        hashes = collect_sha256_hashes(wire.get("node"))
        missing = sorted(h for h in hashes if h not in self._published_hashes)
        if not missing:
            return

        blob_parts: List[tuple] = []
        for h in missing:
            raw = _blob_bytes_from_live_dir(h)
            if raw is None:
                LOGGER.warning("livesync missing blob file for %s", h)
                continue
            blob_parts.append((h, raw))

        if not blob_parts:
            return

        from live_scene_export import build_livescene_hub_blobs_scene_update_message

        msg = build_livescene_hub_blobs_scene_update_message(
            topic=topic,
            blob_parts=blob_parts,
        )
        try:
            status = await client.publish(msg)
            LOGGER.info(
                "livesync incremental scene-update HubBlobs blobs=%d http=%s",
                len(blob_parts),
                status,
            )
            for h, _ in blob_parts:
                self._published_hashes.add(h)
        except Exception as exc:
            LOGGER.warning("livesync incremental HubBlobs failed: %s", exc)


def get_or_create_bridge(
    connection: "ImageDisplayClientConnection",
) -> MrsonHubBridge:
    bridge = getattr(connection, "_mrson_hub_bridge", None)
    if bridge is None:
        bridge = MrsonHubBridge(connection)
        connection._mrson_hub_bridge = bridge
    return bridge
