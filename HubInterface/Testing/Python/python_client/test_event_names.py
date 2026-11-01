#!/usr/bin/env python3
"""Offline asserts for hub_client event-name helpers (no hub required)."""

from __future__ import annotations

import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from common.local_hub import bootstrap_hubinterface_paths  # noqa: E402


def main() -> int:
    bootstrap_hubinterface_paths()
    from hub_client import (
        CONFERENCE_END,
        CONFERENCE_START,
        IMAGINGSTUDY_OPEN,
        STATUS_UPDATE,
        ensure_hub_subscribe_events,
        has_pending_payload,
        hub_event_name,
        hub_payload_id,
        is_own_message,
        is_request_event,
        normalize_data_type,
        request_event_for,
        response_event_for,
    )

    assert normalize_data_type(" STATUS ") == "status"
    # Uppercase aliases still derive the same wire events.
    assert request_event_for("STATUS") == "status-request"
    assert response_event_for("STATUS") == "status-response"
    assert request_event_for("status") == "status-request"
    assert is_request_event("status-request")
    assert not is_request_event("status-update")
    assert hub_event_name({"event": {"hub.event": IMAGINGSTUDY_OPEN}}) == IMAGINGSTUDY_OPEN
    assert CONFERENCE_START == "conference-start"
    assert CONFERENCE_END == "conference-end"

    from hub_client import build_hub_request_event

    built = build_hub_request_event(data_type="STATUS", topic="USER-1")
    assert built["hub.event"] == "status-request"
    assert built["context"]["dataType"] == "status"

    assert ensure_hub_subscribe_events([]) == ["*"]
    assert ensure_hub_subscribe_events(["*"]) == ["*"]
    ensured = ensure_hub_subscribe_events(["dicom-send"])
    assert "dicom-send" in ensured
    assert "status-request" in ensured
    assert STATUS_UPDATE in ensured

    # Standalone / Slicer SP default-style explicit list (status-request already present).
    sp_events = ensure_hub_subscribe_events(
        ["dicom-send", "nifti-send", "status-request"]
    )
    assert "status-request" in sp_events
    assert STATUS_UPDATE in sp_events
    assert sp_events.count("status-request") == 1

    event_pending = {
        "hub.event": "dicom-send",
        "context": {
            "files": [
                {
                    "fileName": "a.dcm",
                    "payloadIds": ["abc"],
                    "byteLength": 4,
                }
            ]
        },
    }
    assert has_pending_payload(event_pending)
    assert hub_payload_id(event_pending) == "abc"

    message_pending = {"id": "m1", "event": event_pending}
    assert has_pending_payload(message_pending)
    assert hub_payload_id(message_pending) == "abc"

    event_ready = {
        "hub.event": "dicom-send",
        "context": {"files": [{"fileName": "a.dcm", "data": b"abcd"}]},
    }
    assert not has_pending_payload(event_ready)
    assert not has_pending_payload({"id": "m2", "event": event_ready})

    assert is_own_message(
        {"subscriber.name": "DEMO-ABC123"}, "DEMO-ABC123"
    )
    assert not is_own_message(
        {"subscriber.name": "OTHER"}, "DEMO-ABC123"
    )
    assert not is_own_message({"subscriber.name": "DEMO-ABC123"}, "")

    print("PASS test_event_names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
