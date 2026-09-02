"""Schemas shared by several routers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Uniform error envelope returned for every handled failure."""

    code: str = Field(description="Stable machine-readable error code")
    message: str
    detail: object | None = None
    request_id: str | None = None


class Page[T](BaseModel):
    """Offset-paginated collection."""

    items: list[T]
    total: int
    limit: int
    offset: int
