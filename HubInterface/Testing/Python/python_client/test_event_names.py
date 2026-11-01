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
        IMAGINGSTUDY_OPEN,
        has_pending_payload,
        hub_event_name,
        is_request_event,
        normalize_data_type,
        request_event_for,
        response_event_for,
    )

    assert normalize_data_type(" STATUS ") == "status"
    assert request_event_for("STATUS") == "status-request"
    assert response_event_for("STATUS") == "status-response"
    assert is_request_event("status-request")
    assert not is_request_event("status-update")
    assert hub_event_name({"event": {"hub.event": IMAGINGSTUDY_OPEN}}) == IMAGINGSTUDY_OPEN

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

    event_ready = {
        "hub.event": "dicom-send",
        "context": {"files": [{"fileName": "a.dcm", "data": b"abcd"}]},
    }
    assert not has_pending_payload(event_ready)

    print("PASS test_event_names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
