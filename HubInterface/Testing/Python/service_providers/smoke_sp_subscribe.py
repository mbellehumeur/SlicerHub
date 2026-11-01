#!/usr/bin/env python3
"""Smoke: subscribe as a service-provider-like session (EC / dicom-send events)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from common.local_hub import (  # noqa: E402
    SMOKE_PRODUCT,
    SMOKE_TOPIC,
    bootstrap_hubinterface_paths,
    build_local_hub_config,
)


async def run() -> None:
    bootstrap_hubinterface_paths()
    from hub_client import (
        HubClientOptions,
        SessionConfig,
        SlicerHubClient,
        generate_subscriber_name,
    )

    # Match ServiceProviderConfig defaults (actors EC, dicom/nifti/status-request).
    hub = build_local_hub_config()
    session = SessionConfig(
        topic=SMOKE_TOPIC,
        subscriber_name=generate_subscriber_name(f"{SMOKE_PRODUCT}-SP"),
        product_name=f"{SMOKE_PRODUCT}-SP",
        product_version="1.0",
        actors=["EC"],
        events=["dicom-send", "nifti-send", "status-request"],
        user_name="3dslicer-server",
        lease=999,
        default_target_actor="EC",
    )
    client = SlicerHubClient(hub, session, HubClientOptions(auto_reconnect=False))
    try:
        auth = await client.authenticate()
        code = str(auth.get("code") or "").strip()
        if not code:
            raise RuntimeError("authenticate returned no code")
        print(f"OK SP authenticate user_name={auth.get('user_name')}")
        if not await client.get_token(code):
            raise RuntimeError("get_token failed")
        status = await client.subscribe()
        if status != 202:
            raise RuntimeError(f"subscribe HTTP {status}")
        print(f"OK SP subscribed as {session.subscriber_name} events={session.events}")

        result = await client.request(
            subscriber=session.subscriber_name,
            topic=SMOKE_TOPIC,
            data_type="STATUS",
            actor="EC",
            target_actor="*",
        )
        if not result.get("ok"):
            raise RuntimeError(f"STATUS request failed: {result}")
        print(f"OK SP STATUS request status={result.get('status')}")
    finally:
        await client.close(hub_unsubscribe=True)


def main() -> int:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"FAIL smoke_sp_subscribe: {exc}")
        return 1
    print("PASS smoke_sp_subscribe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
