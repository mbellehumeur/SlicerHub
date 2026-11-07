#!/usr/bin/env python3
"""Smoke: local hub HTTP reachability + mock OAuth login JSON."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from common.local_hub import (  # noqa: E402
    SMOKE_PRODUCT,
    SMOKE_TOPIC,
    SMOKE_USER,
    bootstrap_hubinterface_paths,
    local_hub_preset,
)


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _post(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _require_json(label: str, body: str) -> dict:
    try:
        data = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"{label} JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{label}: expected object, got {type(data).__name__}")
    return data


def main() -> int:
    bootstrap_hubinterface_paths()
    preset = local_hub_preset()
    hub_endpoint = str(preset["hub_endpoint"]).rstrip("/")
    origin = (
        hub_endpoint.rsplit("/api/hub", 1)[0]
        if "/api/hub" in hub_endpoint
        else hub_endpoint
    )

    print(f"[hub] samples GET {hub_endpoint}/samples ...")
    status, body = _get(f"{hub_endpoint}/samples")
    if status != 200:
        print(f"FAIL samples HTTP {status}: {body[:200]}")
        return 1
    print("OK samples")

    print(f"[hub] auth/config GET {origin}/api/hub/auth/config ...")
    status, body = _get(f"{origin}/api/hub/auth/config")
    if status != 200:
        print(f"FAIL auth/config HTTP {status}: {body[:200]}")
        return 1
    try:
        cfg = _require_json("auth/config", body)
    except RuntimeError as exc:
        print(f"FAIL {exc}")
        return 1
    if str(cfg.get("mode") or "").strip().lower() != "mock":
        print(f"FAIL auth/config expected mode=mock: {cfg!r}")
        return 1
    if cfg.get("authorize_url"):
        print(f"FAIL auth/config still exposes authorize_url: {cfg!r}")
        return 1
    login_url = str(cfg.get("login_url") or "").strip()
    if not login_url:
        print(f"FAIL auth/config missing login_url: {cfg!r}")
        return 1
    print(f"OK auth/config login_url={login_url}")

    login_qs = urllib.parse.urlencode(
        {
            "user_name": SMOKE_USER,
            "topic": SMOKE_TOPIC,
            "client_product_name": SMOKE_PRODUCT,
        }
    )
    print(f"[hub] login GET {login_url} ...")
    status, body = _get(f"{login_url}?{login_qs}")
    if status != 200:
        print(f"FAIL login HTTP {status}: {body[:200]}")
        return 1
    try:
        session = _require_json("login", body)
    except RuntimeError as exc:
        print(f"FAIL {exc}")
        return 1
    access = str(session.get("access_token") or "").strip()
    if not access:
        print(f"FAIL login missing access_token: {session!r}")
        return 1
    if str(session.get("hub_auth") or "").strip().lower() != "mock":
        print(f"FAIL login expected hub_auth=mock: {session!r}")
        return 1
    if str(session.get("topic") or "").strip() != SMOKE_TOPIC:
        print(f"FAIL login topic mismatch: {session!r}")
        return 1
    print(f"OK login access_token={access[:16]}...")

    print(f"[hub] authorize removed POST {origin}/oauth/authorize ...")
    status, body = _post(f"{origin}/oauth/authorize")
    if status not in (404, 405):
        print(f"FAIL expected 404/405 for /oauth/authorize, got {status}: {body[:200]}")
        return 1
    print(f"OK authorize gone HTTP {status}")

    print("PASS smoke_hub_http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
