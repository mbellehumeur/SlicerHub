"""In-memory short-lived HTTP payload store for multipart binary publishes."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from env import HUB_HTTP_PAYLOAD_TTL_SECONDS


class _HttpPayloadEntry:
    """One stored payload (raw bytes + metadata) keyed by random payloadId."""

    __slots__ = (
        "raw",
        "byte_length",
        "expires_at",
        "file_name",
        "mime_type",
        "created_at",
    )

    def __init__(
        self,
        raw: bytes,
        byte_length: int,
        expires_at: datetime,
        file_name: str,
        mime_type: str,
        created_at: datetime,
    ) -> None:
        self.raw = raw
        self.byte_length = byte_length
        self.expires_at = expires_at
        self.file_name = file_name
        self.mime_type = mime_type
        self.created_at = created_at


class _HttpPayloadStore:
    """In-memory payloadId -> bytes store for multipart binary publishes.

    All access happens on the hub's single asyncio loop so we do not need a
    lock. Memory is bounded by ``HUB_HTTP_PAYLOAD_MAX_TOTAL_BYTES``; on
    overflow ``register`` returns ``None`` and the caller fans out
    metadata-only JSON.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, _HttpPayloadEntry] = {}
        self._total_bytes: int = 0

    def register(
        self,
        raw: bytes,
        file_name: str,
        mime_type: str,
        ttl_seconds: int,
        max_total_bytes: int,
    ) -> Optional[tuple]:
        size = len(raw)
        if max_total_bytes > 0 and self._total_bytes + size > max_total_bytes:
            return None
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(int(ttl_seconds), 1))
        self._entries[token] = _HttpPayloadEntry(
            raw=raw,
            byte_length=size,
            expires_at=expires_at,
            file_name=(file_name or "").strip(),
            mime_type=(mime_type or "").strip()
            or "application/octet-stream",
            created_at=now,
        )
        self._total_bytes += size
        return token, expires_at

    def get(self, token: str) -> Optional[_HttpPayloadEntry]:
        entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.expires_at < datetime.now(timezone.utc):
            return None
        return entry

    def evict_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [t for t, e in self._entries.items() if e.expires_at < now]
        for token in expired:
            entry = self._entries.pop(token, None)
            if entry is not None:
                self._total_bytes -= entry.byte_length
        return len(expired)

    def total_bytes(self) -> int:
        return self._total_bytes

    def count(self) -> int:
        return len(self._entries)


_http_payload_store = _HttpPayloadStore()



async def _http_payload_reaper() -> None:
    """Background task: evict expired http payload entries on a fixed cadence."""
    log = logging.getLogger("hub")
    sleep_seconds = max(
        5.0, min(30.0, HUB_HTTP_PAYLOAD_TTL_SECONDS / 4.0)
    )
    while True:
        try:
            await asyncio.sleep(sleep_seconds)
            evicted = _http_payload_store.evict_expired()
            if evicted:
                log.info(
                    "http payload reaper evicted %s entry/entries "
                    "(inflight=%sB, count=%s)",
                    evicted,
                    _http_payload_store.total_bytes(),
                    _http_payload_store.count(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("http payload reaper error: %s", exc)
