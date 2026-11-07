"""Hub logger and uvicorn access-log filtering."""

from __future__ import annotations

import logging
import re

hub_logger = logging.getLogger("hub")


def hub_path_excluded_from_request_log(path: str) -> bool:
    """Hub UI polling paths omitted from hub log and uvicorn access log."""
    if path == "/api/hub/admin" or path.startswith("/api/hub/admin/"):
        return True
    if path.startswith("/api/hub/payloads/"):
        return True
    if path.startswith("/api/hub/samples/files/"):
        return True
    return False


def path_suppress_uvicorn_access_log(path: str, method: str) -> bool:
    """Skip uvicorn access lines when hub middleware already logs the request."""
    if hub_path_excluded_from_request_log(path):
        return True
    if path.startswith("/api/hub") and method in ("GET", "POST", "DELETE"):
        return True
    if path in ("/oauth/token", "/oauth/login", "/oauth/callback", "/api/hub/auth/config") and method in ("POST", "GET"):
        return True
    return False


_UVICORN_ACCESS_PATH_RE = re.compile(r' - "([A-Z]+) ([^\s?]+)')


class UvicornAccessLogFilter(logging.Filter):
    """Suppress duplicate uvicorn access lines (hub middleware logs these)."""

    def filter(self, record: logging.LogRecord) -> bool:
        match = _UVICORN_ACCESS_PATH_RE.search(record.getMessage())
        if not match:
            return True
        method, path = match.group(1), match.group(2)
        return not path_suppress_uvicorn_access_log(path, method)


def _install_uvicorn_access_log_filter() -> None:
    """Apply after uvicorn configures logging (see startup hook below)."""
    filt = UvicornAccessLogFilter()
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, UvicornAccessLogFilter) for f in logger.filters):
        logger.addFilter(filt)
    for handler in logger.handlers:
        if not any(isinstance(f, UvicornAccessLogFilter) for f in handler.filters):
            handler.addFilter(filt)


def _configure_hub_logging() -> None:
    """Ensure hub logger emits INFO+ when no handler is configured yet."""
    if not hub_logger.handlers and not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    hub_logger.setLevel(logging.INFO)
