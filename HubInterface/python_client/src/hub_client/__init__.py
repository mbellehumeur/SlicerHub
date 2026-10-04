"""Hub Python client (wire protocol, WebSocket, binary batch)."""

from __future__ import annotations

import importlib

_client = importlib.import_module("hub_client.client")


def __getattr__(name: str):
    return getattr(_client, name)


def __dir__() -> list[str]:
    return sorted({n for n in dir(_client) if not n.startswith("__")})
