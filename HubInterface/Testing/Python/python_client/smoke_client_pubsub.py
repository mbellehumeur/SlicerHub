#!/usr/bin/env python3
"""Smoke: two hub_client sessions — subscribe, publish, receive, STATUS, dicom-send."""

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
    SMOKE_USER,
    bootstrap_hubinterface_paths,
    build_local_hub_config,
)


async def _connect_client(label: str, events: list[str], *, use_connect: bool = False):
    from hub_client import (
        HubClientOptions,
        SessionConfig,
        SlicerHubClient,
        generate_subscriber_name,
    )

    hub = build_local_hub_config()
    session = SessionConfig(
        topic=SMOKE_TOPIC,
        subscriber_name=generate_subscriber_name(f"{SMOKE_PRODUCT}-{label}"),
        product_name=SMOKE_PRODUCT,
        product_version="1.0",
        actors=["ID"],
        events=events,
        user_name=SMOKE_USER,
        lease=999,
    )
    client = SlicerHubClient(hub, session, HubClientOptions(auto_reconnect=False))
    if use_connect:
        status = await client.connect()
        if status != 202:
            raise RuntimeError(f"{label}: connect HTTP {status}")
    else:
        auth = await client.authenticate()
        if auth.get("tokens_ready") and client.has_token:
            pass
        else:
            code = str(auth.get("code") or "").strip()
            if not code:
                raise RuntimeError(f"{label}: authenticate returned no code")
            if not await client.get_token(code):
                raise RuntimeError(f"{label}: get_token failed")
        status = await client.subscribe()
        if status != 202:
            raise RuntimeError(f"{label}: subscribe HTTP {status}")
    if not client.is_subscribed or not client.has_token:
        raise RuntimeError(f"{label}: expected is_subscribed and has_token")
    if client.subscriber_name != session.subscriber_name:
        raise RuntimeError(f"{label}: subscriber_name accessor mismatch")
    if client.topic != SMOKE_TOPIC:
        raise RuntimeError(f"{label}: topic accessor mismatch")
    print(f"OK {label} subscribed as {client.subscriber_name}")
    return client


async def _await_event(client, want_event: str, timeout: float = 8.0):
    from hub_client import hub_event_name

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {want_event}")
        try:
            msg = await asyncio.wait_for(client.message_queue.get(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"timed out waiting for {want_event}") from exc
        name = hub_event_name(msg)
        if name == want_event:
            return msg


async def run() -> None:
    bootstrap_hubinterface_paths()
    from hub_client import IMAGINGSTUDY_CLOSE

    sub = await _connect_client("sub", ["*"], use_connect=True)
    pub = await _connect_client("pub", ["*"], use_connect=False)
    try:
        close_msg = {
            "event": {
                "hub.topic": SMOKE_TOPIC,
                "hub.event": IMAGINGSTUDY_CLOSE,
                "context": [],
            }
        }
        pub_status = await pub.publish(close_msg)
        print(f"OK publish imagingstudy-close http={pub_status}")
        received = await _await_event(sub, IMAGINGSTUDY_CLOSE)
        print(f"OK sub received imagingstudy-close id={received.get('id')}")

        result = await pub.request(
            subscriber=pub.subscriber_name,
            topic=SMOKE_TOPIC,
            data_type="status",
            actor="ID",
            target_actor="*",
        )
        if not result.get("ok"):
            raise RuntimeError(f"STATUS request failed: {result}")
        print(f"OK STATUS request status={result.get('status')}")

        dicom_bytes = b"DICM" + b"\x00" * 64
        dicom_msg = {
            "event": {
                "hub.topic": SMOKE_TOPIC,
                "hub.event": "dicom-send",
                "context": {
                    "files": [
                        {
                            "fileName": "smoke.dcm",
                            "mimeType": "application/dicom",
                            "data": dicom_bytes,
                        }
                    ]
                },
            }
        }
        dicom_status = await pub.publish(dicom_msg)
        print(f"OK publish dicom-send http={dicom_status}")
        got = await _await_event(sub, "dicom-send")
        print(f"OK sub received dicom-send id={got.get('id')}")
    finally:
        await pub.close(hub_unsubscribe=True)
        await sub.close(hub_unsubscribe=True)


def main() -> int:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"FAIL smoke_client_pubsub: {exc}")
        return 1
    print("PASS smoke_client_pubsub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
