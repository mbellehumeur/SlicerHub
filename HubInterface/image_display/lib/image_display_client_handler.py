"""Image Display Client inbound event handlers (imagingstudy-open / close)."""

from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

from imaging_study_context import (
    extract_open_mode,
    extract_study_display_id,
    normalize_imaging_study_context,
)

if TYPE_CHECKING:
    from image_display_client_hub import ImageDisplayClientConnection

LOGGER = logging.getLogger("HubInterface.ImageDisplay")


def _clear_hub_loaded_study(connection: "ImageDisplayClientConnection") -> None:
    from image_display_client_load import clear_loaded_study

    clear_loaded_study(
        connection.get_loaded_node_ids(),
        connection.get_temp_dir(),
    )
    connection.clear_load_state()


def _is_stale_open_load(
    connection: "ImageDisplayClientConnection", load_generation: int
) -> bool:
    return load_generation != connection.get_open_load_generation()


def _schedule_clear(connection: "ImageDisplayClientConnection") -> None:
    """Always clear the viewport; not tied to open load generation."""

    def run_clear() -> None:
        LOGGER.info("Image Display clearing viewport for imagingstudy-close")
        _clear_hub_loaded_study(connection)
        connection.notify_load_finished()

    connection.schedule_main_thread(run_clear, urgent=True)


def _schedule_load(
    connection: "ImageDisplayClientConnection",
    context: list,
    load_generation: int,
) -> None:
    def run_load() -> None:
        from image_display_client_load import (
            clear_loaded_study,
            load_imaging_study_open,
        )

        if _is_stale_open_load(connection, load_generation):
            LOGGER.info(
                "Image Display skipping stale imagingstudy-open load (generation %s)",
                load_generation,
            )
            return

        LOGGER.info(
            "Image Display clearing viewport before imagingstudy-open load "
            "(generation %s)",
            load_generation,
        )
        _clear_hub_loaded_study(connection)

        if _is_stale_open_load(connection, load_generation):
            return

        result = load_imaging_study_open(context)
        if _is_stale_open_load(connection, load_generation):
            clear_loaded_study(
                result.get("loaded_node_ids") or [],
                str(result.get("temp_dir") or "").strip(),
            )
            LOGGER.info(
                "Image Display discarded stale imagingstudy-open result "
                "(generation %s)",
                load_generation,
            )
            return

        connection.apply_load_result(result)
        connection.notify_load_finished()

    connection.schedule_main_thread(run_load)


def handle_imaging_study_open(
    connection: "ImageDisplayClientConnection",
    message: Dict[str, Any],
) -> Dict[str, Any]:
    context = normalize_imaging_study_context(message)
    event = message.get("event") or {}
    study_id = extract_study_display_id(context)
    open_mode = extract_open_mode(context)
    LOGGER.info(
        "Image Display imagingstudy-open id=%s topic=%s study=%s open_mode=%s",
        message.get("id"),
        event.get("hub.topic", ""),
        study_id or "(unknown)",
        open_mode or "(unspecified)",
    )
    load_generation = connection.next_open_load_generation()
    _schedule_load(connection, list(context), load_generation)
    return {
        "last_open_context": list(context),
        "last_event": "imagingstudy-open",
        "open_study_id": study_id,
        "open_mode": open_mode,
    }


def handle_imaging_study_close(
    connection: "ImageDisplayClientConnection",
    message: Dict[str, Any],
) -> Dict[str, Any]:
    event = message.get("event") or {}
    LOGGER.info(
        "Image Display imagingstudy-close id=%s topic=%s",
        message.get("id"),
        event.get("hub.topic", ""),
    )
    connection.next_open_load_generation()
    _schedule_clear(connection)
    return {
        "last_open_context": [],
        "last_event": "imagingstudy-close",
        "open_study_id": "",
        "open_mode": "",
        "load_status": "",
        "load_error": "",
    }
