#!/usr/bin/env python3
"""Standalone Slicer image display client (no Slicer Hub module UI).

Run:
  Slicer --python-script image_display/run_image_display.py -- --local --topic USER-1
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _bootstrap_paths() -> None:
    module_root = Path(__file__).resolve().parents[1]
    for extra in (
        module_root,
        module_root / "python_client" / "src",
        module_root / "resource_servers" / "runtime",
        module_root / "resource_servers",
        module_root / "image_display" / "lib",
    ):
        s = str(extra)
        if extra.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


def main(argv: list[str] | None = None) -> int:
    _bootstrap_paths()
    import qt
    import slicer  # noqa: F401 — required host app

    from hub_client import generate_subscriber_name
    from hub_presets import HUBS
    from image_display_client_hub import (
        DISPLAY_PRODUCT_NAME,
        DISPLAY_PRODUCT_VERSION,
        ImageDisplayClientConnection,
    )

    parser = argparse.ArgumentParser(description="Hub Slicer image display client")
    parser.add_argument("--local", action="store_true", help="Use SLICER-HUB local preset")
    parser.add_argument("--topic", default="USER-1", help="Hub topic")
    parser.add_argument(
        "--hub",
        default="SLICER-HUB-CLOUD",
        help="Hub preset name (SLICER-HUB or SLICER-HUB-CLOUD)",
    )
    parser.add_argument("--product-name", default=DISPLAY_PRODUCT_NAME)
    args = parser.parse_args(argv)

    hub_name = "SLICER-HUB" if args.local else args.hub
    if hub_name not in HUBS:
        print(f"Unknown hub {hub_name!r}", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO)

    def post_ui(fn: Callable[[], None]) -> None:
        qt.QTimer.singleShot(0, fn)

    def post_ui_urgent(fn: Callable[[], None]) -> None:
        qt.QTimer.singleShot(0, fn)

    def on_status(state: str, detail: Optional[Dict[str, Any]]) -> None:
        logging.info("status %s %s", state, detail or {})

    connection = ImageDisplayClientConnection(post_ui, post_ui_urgent)
    subscriber = generate_subscriber_name(args.product_name or DISPLAY_PRODUCT_NAME)
    connection.connectHub(
        hub_name,
        args.topic,
        subscriber,
        args.product_name,
        DISPLAY_PRODUCT_VERSION,
        status_callback=on_status,
    )
    print(f"Image display connecting as {subscriber} on {hub_name} topic {args.topic}")
    try:
        while True:
            slicer.app.processEvents()
            time.sleep(0.05)
    except KeyboardInterrupt:
        connection.disconnectHub()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
