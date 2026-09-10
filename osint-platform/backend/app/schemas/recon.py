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
    #: Fuller name spellings a public profile declared. Extra searches, never a
    #: replacement for the name the investigator supplied.
    also_known_as: list[str] = Field(default_factory=list)
    #: What may be done with each platform, so "manual only" is explained.
    capabilities: list[SourcePlatformRead] = Field(default_factory=list)
    #: Stated on the plan so the UI never implies the platform will run these.
    execution: str = (
        "These queries are for you to run in your own browser. The platform does not "
        "submit them to any search engine and does not scrape search result pages."
    )


class SourcePlatformRead(BaseModel):
    """One platform and what this codebase may legitimately do with it.

    Sent to the UI so the investigator can see *why* a platform is manual-only
    rather than discovering it from an empty result list.
    """

    platform: str
    display_name: str
    domains: list[str]
    server_fetchable: bool
    public_api_available: bool
    manual_search_supported: bool
    handle_check_supported: bool
    image_reference_supported: bool
    search_filters: list[str]
    notes: str | None = None


class NameVariantRead(BaseModel):
    """One spelling to search for, and why it exists."""

    search_variant: str
    variant_type: str
    variant_label: str
    canonical_target: str
    variant_generation_reason: str
    #: How much this spelling may contribute *as a name match*. A shorter form
    #: is worth less, because more people share it.
    name_weight: float


class ReconStageRead(BaseModel):
    """One stage of the staged plan."""

    stage: int
    title: str
    purpose: str
    queries: list[ReconQueryRead] = Field(default_factory=list)


class DiscoveredAnchorRead(BaseModel):
    """Something a public source published about a candidate, with its source."""

    kind: str
    value: str
    source_url: str
    source_label: str = ""


class StagedReconPlan(BaseModel):
    """The plan an investigator actually works through.

    Staged rather than flat: the canonical name, then the spellings a source
    might use, then what the investigator already knows, then what the
    investigation discovered, then the broad sweeps.
    """

    target_id: uuid.UUID
    canonical: str
    variants: list[NameVariantRead] = Field(default_factory=list)
    stages: list[ReconStageRead] = Field(default_factory=list)
    discovered_anchors: list[DiscoveredAnchorRead] = Field(default_factory=list)
    anchors_used: list[str] = Field(default_factory=list)
    also_known_as: list[str] = Field(default_factory=list)
    capabilities: list[SourcePlatformRead] = Field(default_factory=list)
    #: What the public-web channel can do in this deployment right now.
    search_provider: str = "none"
    search_provider_configured: bool = False
    search_provider_note: str = ""
    execution: str = (
        "Queries in this plan are for you to run in your own browser unless a search "
        "provider is configured. The platform never scrapes a search result page."
    )


class SearchIngestRead(BaseModel):
    """What one automated ingestion run did."""

    provider: str
    configured: bool
    reason: str | None = None
    queries_run: int = 0
    results_seen: int = 0
    results_stored: int = 0
    duplicates: int = 0
    rejected_urls: int = 0
    failures: list[dict] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


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
    #: The organisation the result showed. Recorded as the page's claim and run
    #: through the same anchor comparison a collected affiliation is.
    organization: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    image_url: str | None = Field(default=None, max_length=2048)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    caption: str | None = Field(default=None, max_length=1000)
    #: The handle shown on the result, when the investigator saw one. Used only
    #: where the URL's own shape did not already yield it — a handle typed into
    #: a form is a claim about the page, and the page's URL is the better
    #: source when it has one.
    handle: str | None = Field(default=None, max_length=200)
    #: The name displayed on the result. Recorded as what the page shows; it
    #: never becomes the target's name.
    display_name: str | None = Field(default=None, max_length=300)


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
