"""Register all hub API routers on the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from routes import (
    admin,
    bind_ws,
    conference,
    hub_api,
    idc,
    misc,
    oauth,
    payloads,
    samples,
)


def register_all(app: FastAPI) -> None:
    """Include every hub route module."""
    for module in (
        hub_api,
        bind_ws,
        admin,
        oauth,
        conference,
        payloads,
        samples,
        idc,
        misc,
    ):
        app.include_router(module.router)
