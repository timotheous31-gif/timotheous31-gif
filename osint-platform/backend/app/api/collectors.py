"""Collector registry endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.collectors.registry import collector_metadata, load_builtin_collectors
from app.schemas.finding import CollectorInfo

router = APIRouter(prefix="/collectors", tags=["collectors"])


@router.get("", response_model=list[CollectorInfo], summary="List available collectors")
def list_collectors() -> list[CollectorInfo]:
    """Every registered collector, with whether it can currently run.

    A collector that needs an unconfigured API key reports ``available: false``
    and says which variable to set, rather than silently producing nothing.
    """
    load_builtin_collectors()
    return [CollectorInfo(**entry) for entry in collector_metadata()]
