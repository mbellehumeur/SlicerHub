#!/usr/bin/env python3
"""Smoke: local hub HTTP reachability + mock OAuth authorize/token."""

from __future__ import annotations

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


def _post_form(url: str, fields: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    bootstrap_hubinterface_paths()
    preset = local_hub_preset()
    hub_endpoint = str(preset["hub_endpoint"]).rstrip("/")
    authorize = str(preset["authorization_endpoint"])
    token = str(preset["token_endpoint"])
    client_id = str(preset["client_id"])
    client_secret = str(preset["client_secret"])

    print(f"[hub] samples GET {hub_endpoint}/samples ...")
    status, body = _get(f"{hub_endpoint}/samples")
    if status != 200:
        print(f"FAIL samples HTTP {status}: {body[:200]}")
        return 1
    print("OK samples")

    print(f"[hub] authorize POST {authorize} ...")
    status, body = _post_form(
        authorize,
        {
            "client_product_name": SMOKE_PRODUCT,
            "user_name": SMOKE_USER,
            "topic": SMOKE_TOPIC,
        },
    )
    if status != 200:
        print(f"FAIL authorize HTTP {status}: {body[:200]}")
        return 1
    try:
        import json

        auth = json.loads(body)
    except Exception as exc:
        print(f"FAIL authorize JSON: {exc}")
        return 1
    code = str(auth.get("code") or "").strip()
    if not code:
        print(f"FAIL authorize missing code: {auth!r}")
        return 1
    print(f"OK authorize code={code[:12]}...")

    print(f"[hub] token POST {token} ...")
    status, body = _post_form(
        token,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "client_product_name": SMOKE_PRODUCT,
        },
    )
    if status != 200:
        print(f"FAIL token HTTP {status}: {body[:200]}")
        return 1
    try:
        tok = json.loads(body)
    except Exception as exc:
        print(f"FAIL token JSON: {exc}")
        return 1
    access = str(tok.get("access_token") or "").strip()
    if not access:
        print(f"FAIL token missing access_token: {tok!r}")
        return 1
    print(f"OK token access_token={access[:16]}...")
    print("PASS smoke_hub_http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
