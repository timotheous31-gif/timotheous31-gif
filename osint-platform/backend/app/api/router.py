"""Aggregate API router.

Routers are imported lazily inside :func:`build_api_router` so importing this
module (for example from the CLI) does not pull in the whole service layer.
"""

from __future__ import annotations

from fastapi import APIRouter


def build_api_router() -> APIRouter:
    """Assemble the versioned API router."""
    from app.api import cases, targets

    router = APIRouter()
    router.include_router(cases.router)
    router.include_router(targets.router)
    return router
