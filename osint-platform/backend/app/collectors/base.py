"""The collector contract.

A collector is a small, self-contained adapter over one public data source. It
declares what it can accept, fetches raw data through the shared HTTP client (so
the SSRF, rate-limit and size controls always apply), and turns that raw data
into :class:`FindingDraft` objects.

Two rules are structural rather than advisory:

1. **A collector never persists anything.** It returns drafts; the engine
   classifies them through the privacy filter, stores evidence and writes rows.
2. **A collector never suppresses its own failure.** It raises, and the runner
   records the failure on the run row so an investigation continues with the
   collectors that did work.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.core.errors import CollectorUnavailable
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.core.settings import Settings, get_settings
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)


@dataclass(slots=True)
class RawPayload:
    """One raw artefact retrieved from a source, kept for the evidence store."""

    #: What was fetched — a URL, or a synthetic identifier for non-HTTP sources
    #: such as ``dns://example.com/MX``.
    source_url: str
    #: The payload itself. Serialised to JSON for hashing and storage.
    content: Any
    content_type: str = "application/json"
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status_code: int | None = None


@dataclass(slots=True)
class FindingDraft:
    """A normalised assertion, before privacy filtering and persistence."""

    kind: FindingKind
    title: str
    data: dict[str, Any]
    summary: str | None = None
    source_url: str | None = None
    confidence: float = 0.7
    confidence_reasons: list[str] = field(default_factory=list)
    #: The collector's own view of sensitivity. The privacy filter may raise it,
    #: never lower it.
    classification: Classification = Classification.PUBLIC
    #: When the described fact happened, if the source says so.
    observed_at: datetime | None = None
    #: Stable identity used to avoid storing the same assertion twice.
    dedupe_key: str | None = None
    #: Index into :attr:`CollectorResult.payloads` for the supporting artefact.
    payload_index: int | None = None

    def key(self, collector: str) -> str:
        """Return the dedupe key, deriving a stable one when none was given."""
        if self.dedupe_key:
            return self.dedupe_key[:128]
        import hashlib
        import json

        digest = hashlib.sha256(
            json.dumps(
                {"c": collector, "k": str(self.kind), "d": self.data}, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        return digest[:128]


@dataclass(slots=True)
class CollectorResult:
    """Everything one collector run produced."""

    findings: list[FindingDraft] = field(default_factory=list)
    payloads: list[RawPayload] = field(default_factory=list)
    #: Counters surfaced on the run row (requests made, records seen, ...).
    stats: dict[str, Any] = field(default_factory=dict)
    #: Non-fatal notes, e.g. "NXDOMAIN for AAAA". Shown to the investigator.
    notes: list[str] = field(default_factory=list)

    def add(self, finding: FindingDraft, payload: RawPayload | None = None) -> FindingDraft:
        """Attach ``finding``, linking it to ``payload`` when one is supplied."""
        if payload is not None:
            if payload not in self.payloads:
                self.payloads.append(payload)
            finding.payload_index = self.payloads.index(payload)
            finding.source_url = finding.source_url or payload.source_url
        self.findings.append(finding)
        return finding


@dataclass(slots=True)
class CollectorConfiguration:
    """How a collector is configured right now, for the operator-facing UI.

    This carries setting *names* and a status — never a credential value, and
    never anything derived from one. It exists so that "search is skipped" can
    be answered with "set SEARCH_PROVIDER and BRAVE_API_KEY" instead of leaving
    an investigator to guess which of three keys the platform wanted.
    """

    #: Settings that must be present before the collector can run at all.
    required_settings: list[str] = field(default_factory=list)
    #: Settings that change how the collector runs but are not required.
    optional_settings: list[str] = field(default_factory=list)
    #: True when every required setting is present.
    configured: bool = True
    #: Short label for the operating mode ("unauthenticated", "brave", ...).
    mode: str = ""
    #: One sentence an operator can act on.
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_settings": list(self.required_settings),
            "optional_settings": list(self.optional_settings),
            "configured": self.configured,
            "mode": self.mode,
            "detail": self.detail,
        }


@dataclass(slots=True)
class CollectorContext:
    """Ambient state a collector may consult during a run."""

    case_id: uuid.UUID
    target_id: uuid.UUID | None = None
    settings: Settings = field(default_factory=get_settings)
    #: Findings already produced in this investigation, so a collector can
    #: build on earlier work (e.g. certificate transparency feeding HTTP).
    prior: dict[str, Any] = field(default_factory=dict)


class BaseCollector(abc.ABC):
    """Base class every collector implements."""

    #: Unique registry key, also used as the rate-limit provider key.
    name: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""
    supported_targets: ClassVar[list[TargetType]] = []
    #: True when the collector cannot run without a configured credential.
    requires_api_key: ClassVar[bool] = False
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=5, per_seconds=1.0, concurrency=4)
    #: Per-request timeout passed to the HTTP client.
    timeout: ClassVar[float] = 20.0
    #: Hard ceiling on one whole run (all requests). ``None`` uses
    #: ``COLLECTOR_TIMEOUT_SECONDS``. A collector that makes many requests keeps
    #: a small per-request ``timeout`` and a larger run budget.
    run_timeout: ClassVar[float | None] = None
    retry: ClassVar[RetryPolicy] = RetryPolicy(attempts=3, base_delay=0.5)
    #: Baseline confidence for findings that do not set their own.
    default_confidence: ClassVar[float] = 0.7
    #: Human-readable attribution shown in the report's Sources section.
    source_attribution: ClassVar[str] = ""
    #: Set to False for collectors that make no outbound requests.
    network: ClassVar[bool] = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ----------------------------------------------------------- capability

    @classmethod
    def accepts(cls, target_type: TargetType) -> bool:
        """True when this collector can run against ``target_type``."""
        return target_type in cls.supported_targets

    def is_available(self) -> tuple[bool, str]:
        """Whether the collector can run now, and why not when it cannot.

        Collectors that need a credential override this. The engine records a
        ``SKIPPED`` run with the reason rather than failing the investigation,
        so a missing key is visible instead of silently producing nothing.
        """
        return True, ""

    def ensure_available(self) -> None:
        """Raise :class:`CollectorUnavailable` when the collector cannot run."""
        available, reason = self.is_available()
        if not available:
            raise CollectorUnavailable(reason or f"{self.name} is not available")

    def configuration(self) -> CollectorConfiguration:
        """Which settings this collector uses, and whether they are present.

        Collectors that read a credential override this so the Collectors and
        Settings pages can name the variable to set. Overrides must return
        setting names and status only.
        """
        return CollectorConfiguration()

    # -------------------------------------------------------------- contract

    @abc.abstractmethod
    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        """Fetch from the source and return normalised findings.

        Implementations should call :meth:`normalize` (or inline the same
        logic) so that raw payloads and findings stay in step, and must let
        genuine failures propagate.
        """

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        """Turn one raw payload into findings.

        Split out from :meth:`collect` so it can be unit-tested against fixture
        payloads with no network at all.
        """
        raise NotImplementedError

    # --------------------------------------------------------------- helpers

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Registry metadata, surfaced by ``GET /collectors`` and the CLI."""
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.description,
            "supported_targets": [str(item) for item in cls.supported_targets],
            "requires_api_key": cls.requires_api_key,
            "rate_limit": cls.rate_limit.describe(),
            "timeout": cls.timeout,
            "run_timeout": cls.run_timeout,
            "source_attribution": cls.source_attribution,
            "network": cls.network,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name} v{self.version}>"
