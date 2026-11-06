"""Publish MetaQuest mesh snapshots (glb-send) from the Image Display client."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

LOGGER = logging.getLogger("HubInterface.ImageDisplay.Mesh")

if TYPE_CHECKING:
    from hub_client import SlicerHubClient
    from image_display_client_hub import ImageDisplayClientConnection

_SNAPSHOT_DEBOUNCE_MS = 750
GLB_DATA_TYPE = "glb"


class ImageDisplayMeshBridge:
    """Debounced GLB publisher bound to one Image Display Hub connection."""

    def __init__(
        self,
        connection: "ImageDisplayClientConnection",
        post_ui: Callable[[Callable[[], None]], None],
    ) -> None:
        self._connection = connection
        self._post_ui = post_ui
        self._timer = None
        self._snapshot_version = 0
        self._last_node_state: Dict[str, Any] = {}
        self._publishing = False
        self._observers: list = []
        self._pending_publish = False

    def start(self) -> None:
        self._attach_observers()
        self.schedule_snapshot("start")

    def stop(self) -> None:
        self._detach_observers()
        self._cancel_timer()

    def schedule_snapshot(self, reason: str = "") -> None:
        LOGGER.debug("mesh snapshot scheduled reason=%s", reason or "-")
        self._cancel_timer()
        try:
            import qt

            timer = qt.QTimer()
            timer.setSingleShot(True)
            timer.setInterval(_SNAPSHOT_DEBOUNCE_MS)

            def fire() -> None:
                self._timer = None
                self._pending_publish = True

            timer.connect("timeout()", fire)
            self._timer = timer
            timer.start()
        except Exception as exc:
            LOGGER.warning("mesh debounce timer failed: %s", type(exc).__name__)
            self._pending_publish = True

    def consume_pending(self) -> bool:
        """Return True once if a snapshot should be published (polled from hub loop)."""
        if not self._pending_publish or self._publishing:
            return False
        self._pending_publish = False
        return True

    def _cancel_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is None:
            return
        try:
            timer.stop()
        except Exception:
            pass

    async def publish_snapshot_now(self, client: "SlicerHubClient") -> None:
        if self._publishing:
            return
        self._publishing = True
        try:
            from image_display_mesh_glb import build_glb_send_message

            topic = client.topic or self._connection.get_topic()

            def export_on_main():
                self._snapshot_version += 1
                return build_glb_send_message(
                    topic=topic, snapshot_version=self._snapshot_version
                )

            msg, count, node_state = await self._connection.run_on_main_thread(
                export_on_main
            )
            self._last_node_state = node_state
            status = await client.publish(msg)
            LOGGER.info(
                "published glb-send version=%s entities=%s http=%s",
                self._snapshot_version,
                count,
                status,
            )
        except Exception as exc:
            LOGGER.warning("glb-send publish failed: %s", exc)
        finally:
            self._publishing = False

    async def handle_glb_request(
        self, client: "SlicerHubClient", message: Dict[str, Any]
    ) -> None:
        from hub_client import request_context

        correlation = ""
        context = request_context(message)
        cid = context.get("id")
        if isinstance(cid, str) and cid.strip():
            correlation = cid.strip()

        LOGGER.info("glb-request received — exporting snapshot")
        try:
            await self.publish_snapshot_now(client)
            if correlation:
                client.send_hub_request_response(
                    correlation,
                    GLB_DATA_TYPE,
                    {
                        "source": "glb",
                        "ok": True,
                        "snapshotVersion": self._snapshot_version,
                        "product": self._connection.get_product_name(),
                    },
                    (message.get("event") or {}).get("hub.topic"),
                )
        except Exception as exc:
            LOGGER.warning("glb-request failed: %s", exc)
            if correlation:
                client.send_hub_request_response(
                    correlation,
                    GLB_DATA_TYPE,
                    {
                        "source": "glb",
                        "ok": False,
                        "error": str(exc).strip() or type(exc).__name__,
                    },
                    (message.get("event") or {}).get("hub.topic"),
                )

    def _attach_observers(self) -> None:
        self._detach_observers()
        try:
            import slicer

            scene = slicer.mrmlScene

            def on_any(_caller=None, _event=None) -> None:
                self.schedule_snapshot("mrml")

            for event in (
                slicer.vtkMRMLScene.NodeAddedEvent,
                slicer.vtkMRMLScene.NodeRemovedEvent,
                slicer.vtkMRMLScene.EndImportEvent,
                slicer.vtkMRMLScene.EndCloseEvent,
            ):
                tag = scene.AddObserver(event, on_any)
                self._observers.append((scene, tag))
        except Exception as exc:
            LOGGER.warning("mesh observers not attached: %s", type(exc).__name__)

    def _detach_observers(self) -> None:
        for obj, tag in self._observers:
            try:
                obj.RemoveObserver(tag)
            except Exception:
                pass
        self._observers = []
