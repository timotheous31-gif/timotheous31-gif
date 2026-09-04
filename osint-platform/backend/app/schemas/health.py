"""Health endpoint schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str = Field(examples=["ok", "degraded"])
    checks: dict[str, str]
