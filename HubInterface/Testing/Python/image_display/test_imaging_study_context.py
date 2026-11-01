#!/usr/bin/env python3
"""Offline asserts for image_display imaging_study_context (no Slicer / hub)."""

from __future__ import annotations

import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from common.local_hub import bootstrap_hubinterface_paths  # noqa: E402


def main() -> int:
    bootstrap_hubinterface_paths()
    from imaging_study_context import (
        HUB_OPEN_MODE_DICOMWEB,
        HUB_OPEN_MODE_FILES,
        HUB_OPEN_MODE_LOCAL_DICOM,
        extract_open_mode,
        resolve_imaging_study_open_plan,
    )

    files_context = [
        {
            "key": "study",
            "resource": {
                "id": "demo-1",
                "identifier": [
                    {"system": "urn:cast:open-mode", "value": "files"},
                ],
            },
        },
        {
            "key": "files",
            "resource": {
                "files": [
                    {
                        "url": "https://example.com/vol.nrrd",
                        "fileName": "vol.nrrd",
                    }
                ]
            },
        },
    ]
    assert extract_open_mode(files_context) == HUB_OPEN_MODE_FILES
    plan = resolve_imaging_study_open_plan(files_context)
    assert plan is not None
    assert plan["mode"] == HUB_OPEN_MODE_FILES
    assert plan["study_id"] == "demo-1"
    assert len(plan.get("files") or []) == 1

    dicomweb_context = [
        {
            "key": "study",
            "resource": {
                "id": "study-uid-demo",
                "uid": "1.2.3",
                "identifier": [
                    {"system": "urn:cast:open-mode", "value": "dicomweb"},
                    {
                        "system": "urn:cast:dicomweb-root",
                        "value": "https://example.com/dicomweb",
                    },
                ],
            },
        }
    ]
    plan2 = resolve_imaging_study_open_plan(dicomweb_context)
    assert plan2 is not None
    assert plan2["mode"] == HUB_OPEN_MODE_DICOMWEB
    assert plan2["study_uid"] == "1.2.3"
    assert plan2["dicomweb_root"] == "https://example.com/dicomweb"

    local_context = [
        {
            "key": "study",
            "resource": {
                "id": "slicer-dicom:1.2.3",
                "uid": "1.2.3",
                "identifier": [
                    {"system": "urn:cast:open-mode", "value": "local-dicom"},
                ],
            },
        }
    ]
    assert extract_open_mode(local_context) == HUB_OPEN_MODE_LOCAL_DICOM

    print("PASS test_imaging_study_context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
