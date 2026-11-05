"""Browser + loopback helper for hub-mediated OIDC (native / desktop clients)."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from aiohttp import web

LOGGER = logging.getLogger("hub_client.oidc_browser")

_SUCCESS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Hub login</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem">
<h1>Login complete</h1>
<p>You can close this tab and return to the application.</p>
</body></html>
"""


async def capture_hub_oidc_tokens(
    *,
    login_url: str,
    timeout_s: float = 300.0,
    open_browser: bool = True,
) -> Dict[str, str]:
    """Open hub ``/oauth/login`` with ``response_mode=query`` and capture tokens.

    ``login_url`` should already be the hub login endpoint (no query), e.g.
    ``https://host/oauth/login``. This function appends ``return_to`` and
    ``response_mode=query``.
    """
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[Dict[str, str]] = loop.create_future()

    async def handle_callback(request: web.Request) -> web.Response:
        params = {k: str(v) for k, v in request.rel_url.query.items()}
        if params.get("hub_auth") != "oidc" or not params.get("access_token"):
            return web.Response(
                text="Waiting for hub OIDC login…",
                content_type="text/plain",
            )
        if not result_future.done():
            result_future.set_result(params)
        return web.Response(text=_SUCCESS_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", handle_callback)
    app.router.add_get("/{path:.*}", handle_callback)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        sockets = site._server.sockets if site._server else None  # type: ignore[attr-defined]
        if not sockets:
            raise RuntimeError("OIDC loopback server failed to bind")
        port = int(sockets[0].getsockname()[1])
        return_to = f"http://127.0.0.1:{port}/"
        query = urlencode(
            {
                "return_to": return_to,
                "response_mode": "query",
            }
        )
        full_login = f"{login_url.rstrip('/')}?{query}"
        LOGGER.info("OIDC browser login: opening %s", full_login.split("?", 1)[0])
        if open_browser:
            opened = webbrowser.open(full_login)
            if not opened:
                LOGGER.warning(
                    "webbrowser.open returned False; open this URL manually: %s",
                    full_login,
                )
        else:
            LOGGER.info("OIDC open_browser=False; visit: %s", full_login)

        try:
            tokens = await asyncio.wait_for(result_future, timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"OIDC browser login timed out after {timeout_s:.0f}s"
            ) from exc
        return tokens
    finally:
        await runner.cleanup()


def apply_oidc_session_tokens(
    tokens: Dict[str, Any],
) -> Dict[str, str]:
    """Normalize loopback/query token dict for the hub client."""
    access = str(tokens.get("access_token") or "").strip()
    if not access:
        raise RuntimeError("OIDC callback missing access_token")
    return {
        "access_token": access,
        "id_token": str(tokens.get("id_token") or "").strip(),
        "topic": str(tokens.get("topic") or "").strip(),
        "user_name": str(tokens.get("user_name") or "").strip(),
        "expires_in": str(tokens.get("expires_in") or "").strip(),
    }
