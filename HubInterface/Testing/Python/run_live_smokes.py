#!/usr/bin/env python3
"""Run HubInterface Testing smokes (live hub + offline helpers)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent

SCRIPTS = (
    PYTHON_ROOT / "hub" / "smoke_hub_http.py",
    PYTHON_ROOT / "python_client" / "smoke_client_pubsub.py",
    PYTHON_ROOT / "service_providers" / "smoke_sp_subscribe.py",
    PYTHON_ROOT / "image_display" / "test_imaging_study_context.py",
    PYTHON_ROOT / "python_client" / "test_event_names.py",
)


def main() -> int:
    failed = 0
    for script in SCRIPTS:
        print("=" * 60)
        print(f"RUN {script.relative_to(PYTHON_ROOT)}")
        print("=" * 60)
        proc = subprocess.run([sys.executable, str(script)], cwd=str(PYTHON_ROOT))
        if proc.returncode != 0:
            failed += 1
            print(f"FAILED ({proc.returncode}): {script.name}")
        else:
            print(f"PASSED: {script.name}")
    print("=" * 60)
    if failed:
        print(f"SUMMARY: {failed}/{len(SCRIPTS)} failed")
        return 1
    print(f"SUMMARY: all {len(SCRIPTS)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
