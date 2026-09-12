"""Google Web Search Service — adapter and configuration, pending partner access.

This provider exists so that the day credentials arrive is a configuration change
and not a redesign. Until then it is honest about its state: it can be selected,
inspected and reported on, and it will not issue a request.

``PENDING_PARTNER_ACCESS`` is enforced in code, not left to a comment.
:meth:`is_available` returns ``False`` with that reason whatever credentials are
present, and both entry points raise rather than attempt a call. Three things that
would be easy and wrong are therefore impossible here:

* **Faking access.** No stubbed results, no sample data pretending to be a live
  index. An unavailable channel reports itself unavailable, and the coverage table
  says so.
* **Guessing the contract.** The request and response shapes of the partner API
  are not public, so nothing here invents them. :func:`normalise` is written
  against the field names the adapter will be *given*, isolated in one function,
  and it is the only thing that has to change when the real schema is known.
* **Scraping Google instead.** Parsing Google's HTML result pages violates its
  terms and is explicitly out of scope for this platform. There is no fallback
  path from this provider to an HTML scrape, and there must never be one.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit
from app.services.providers.search import (
    OUTCOME_UNAVAILABLE,
    SearchBatch,
    SearchBrief,
    SearchProvider,
    SearchResult,
)

log = get_logger(__name__)

#: The one string that decides this provider's behaviour.
PENDING = "PENDING_PARTNER_ACCESS"

BLOCKERS = (
    "Partner credentials (GOOGLE_WSS_API_KEY, GOOGLE_WSS_CLIENT_ID) have not been " "issued.",
    "The service endpoint is not published, so GOOGLE_WSS_ENDPOINT has no documented "
    "value to default to.",
    "The request and response schemas are under partner agreement and are not "
    "public, so the result mapping cannot be finalised.",
    "Commercial terms for storing and displaying results in an investigation "
    "product have not been reviewed for this service.",
)


class GoogleWebSearchServiceProvider(SearchProvider):
    """Google Web Search Service. Configured, adapter-ready, not activated."""

    key = "google_wss"
    display_name = "Google Web Search Service"
    api_key_setting = "google_wss_api_key"
    rate_limit = RateLimit(requests=1, per_seconds=1.0, concurrency=1)
    activation_state: ClassVar[str] = PENDING
    #: Both true of the real service, declared now so the ingestion layer needs no
    #: change when it is switched on.
    runs_requested_query: ClassVar[bool] = True
    supplies_snippet: ClassVar[bool] = True
    position_is_rank: ClassVar[bool] = True
    provenance_note: ClassVar[str] = (
        "Google Web Search Service names its own index, so when activated the "
        "retrieval channel is recorded as google_wss."
    )

    def is_available(self) -> tuple[bool, str]:
        """Never available, whatever is configured.

        Deliberately not "available once a key is set": a key alone does not make
        the channel usable, and every blocker below is outside this repository.
        """
        return False, (f"{self.display_name} is {PENDING}. " + " ".join(BLOCKERS))

    def configuration_state(self) -> dict[str, Any]:
        """What is configured, without revealing any of it.

        Booleans only. A credential's presence is useful to an operator; its value
        is never returned, logged or rendered.
        """
        return {
            "provider": self.key,
            "activation_state": PENDING,
            "api_key_configured": bool(self.api_key()),
            "client_id_configured": bool(self.settings.google_wss_client_id),
            "endpoint_configured": bool(self.settings.google_wss_endpoint),
            "blockers": list(BLOCKERS),
        }

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        raise ConfigurationError(self.is_available()[1], detail={"activation_state": PENDING})

    async def discover(self, brief: SearchBrief) -> SearchBatch:
        """Report the channel as unavailable. No request is made."""
        log.info("google_wss.not_activated", planned=len(brief.planned))
        return SearchBatch(outcome=OUTCOME_UNAVAILABLE, reason=self.is_available()[1])


def normalise(item: dict[str, Any], *, position: int, query: str) -> SearchResult | None:
    """Map one partner result onto a platform result.

    The single place that has to change when the real schema is documented. Field
    names here are the adapter's working assumption and nothing depends on them
    being right yet — no code path reaches this function until the provider is
    activated, which is why it is written, tested against a fixture, and inert.
    """
    url = str(item.get("url") or item.get("link") or "").strip()
    if not url:
        return None
    description = item.get("snippet")
    if description is None:
        description = item.get("description")
    return SearchResult(
        title=str(item.get("title") or "")[:300],
        url=url,
        provider=GoogleWebSearchServiceProvider.key,
        # Absent stays absent. A service that documents descriptions may still
        # omit one for a given result, and that is not an empty description.
        snippet=str(description)[:500] if description is not None else None,
        provider_position=position,
        position_is_rank=True,
        query=query,
        displayed_url=str(item.get("displayLink") or "")[:300],
    )
