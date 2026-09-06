"""Schemas for the reconnaissance workflow."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReconQueryRead(BaseModel):
    """One generated search, and why it is worth running."""

    query: str
    family: str
    rationale: str
    priority: int
    anchors_used: list[str] = Field(default_factory=list)


class ReconQueryPlan(BaseModel):
    """Everything the Recon Queries view needs."""

    target_id: uuid.UUID
    subject_name: str
    queries: list[ReconQueryRead]
    #: Anchors the queries were built from, for display. Values, not secrets.
    anchors_used: list[str] = Field(default_factory=list)
    #: Stated on the plan so the UI never implies the platform will run these.
    execution: str = (
        "These queries are for you to run in your own browser. The platform does not "
        "submit them to any search engine and does not scrape search result pages."
    )


class ManualResult(BaseModel):
    """One public result an investigator chose to keep.

    ``image_url`` or ``thumbnail_url`` turns the record into image evidence: the
    same import path, filed as visual context rather than as a text result.
    """

    query: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=500)
    snippet: str = Field(default="", max_length=2000)
    #: Which engine the investigator used. Free text: they know, the platform does not.
    engine: str = Field(default="unspecified", max_length=100)
    result_type: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    image_url: str | None = Field(default=None, max_length=2048)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    caption: str | None = Field(default=None, max_length=1000)


class ManualResultImport(BaseModel):
    results: list[ManualResult] = Field(min_length=1, max_length=100)


class ImportedResultRead(BaseModel):
    """An imported result as the UI renders it."""

    id: uuid.UUID
    url: str
    title: str
    snippet: str
    query: str
    engine: str
    platform: str | None = None
    platform_label: str | None = None
    url_kind: str | None = None
    handle: str | None = None
    is_image: bool = False
    image_url: str | None = None
    thumbnail_url: str | None = None
    caption: str | None = None
    evidence_class: str
    imported_at: datetime | None = None
    confidence: float
    #: SHA-256 of the stored artefact, so an import can be shown to be intact.
    evidence_sha256: list[str] = Field(default_factory=list)
