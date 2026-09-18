"""Anthropic's server-side web search, as a bounded secondary discovery channel.

This is deliberately *not* modelled as a traditional search API, because it is
not one. Three documented properties decide how it is used here, and every one of
them is a constraint rather than a preference:

**The model chooses the queries.** The tool definition has no parameter that
submits an exact query string; Anthropic's documentation says triggering "is
steerable through your system prompt" and that "for a hard constraint, use
``max_uses``". So the recon plan is sent as a *brief*, and what comes back
records the query Anthropic actually executed, read from
``server_tool_use.input.query``. A planned query is never written down as an
executed one.

**Results carry no description.** A ``web_search_result`` is ``url``, ``title``,
``page_age`` and ``encrypted_content`` — and nothing else. There is no snippet
field, no rank, no displayed source label and no result classification. Nothing
here invents any of them: :attr:`SearchResult.snippet` stays ``None``, which the
ingestion layer reads as "this provider publishes no description" and treats as a
low-context discovery candidate.

**Searches are billed and capped per request.** ``max_uses`` is the spend cap,
enforced by Anthropic: exceeding it returns a ``max_uses_exceeded`` tool-result
error, and a search that errors is not billed. The count actually executed comes
back in ``usage.server_tool_use.web_search_requests``, which is what the cost
*estimate* is computed from.

Two things are deliberately not done.

``encrypted_content`` is **never persisted**. It is opaque, it is large, and it
only exists to be replayed verbatim on a following turn of the same conversation.
It is held in memory exactly long enough to replay a ``pause_turn`` and is then
dropped, so it cannot reach a finding, a report or the frontend.

**Claude's prose is never evidence.** Only ``web_search_tool_result`` blocks are
read. The model's answer text, and its citations, are discarded: evidence in this
platform points at a public source URL, and a model's summary of that page is not
the page.

Tool version: ``web_search_20250305``, the basic version, with
``allowed_callers: ["direct"]`` set explicitly. That combination is chosen so the
raw result blocks arrive in the response unfiltered — dynamic filtering
(``web_search_20260209`` and later) runs the search from inside code execution
and lets model-written code discard results before the platform ever sees them,
and ``web_search_20260318``'s ``response_inclusion: "excluded"`` removes the
blocks entirely. The basic version is also the one Anthropic documents as
zero-data-retention eligible. See ``docs/search-provider-compliance.md``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.core import http
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.providers.search import (
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_RATE_LIMITED,
    OUTCOME_UNAVAILABLE,
    PlannedQuery,
    ProviderAccounting,
    SearchBatch,
    SearchBrief,
    SearchProvider,
    SearchResult,
)

log = get_logger(__name__)

#: Anything shaped like an API credential, for redaction. Deliberately broad.
_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}", re.IGNORECASE)

#: The tool version pinned, and why. Surfaced in run stats and in the report so a
#: reader never has to guess which variant produced a result.
TOOL_TYPE = "web_search_20250305"
TOOL_NAME = "web_search"
#: Explicit, though it is this version's default. Stated so a later version
#: change cannot silently switch the channel to code-execution filtering.
ALLOWED_CALLERS = ("direct",)

#: Tool-result error codes Anthropic documents. They arrive *inside an HTTP 200*,
#: which is the trap this mapping exists to avoid: none of them means "the public
#: web returned nothing".
ERROR_OUTCOMES: dict[str, str] = {
    "max_uses_exceeded": OUTCOME_BUDGET_EXHAUSTED,
    "too_many_requests": OUTCOME_RATE_LIMITED,
    "unavailable": OUTCOME_UNAVAILABLE,
    "invalid_tool_input": OUTCOME_FAILED,
    "query_too_long": OUTCOME_FAILED,
    "request_too_large": OUTCOME_FAILED,
}

#: Worst-first, so one error in a multi-search response decides the outcome.
_OUTCOME_RANK = {
    OUTCOME_FAILED: 0,
    OUTCOME_UNAVAILABLE: 1,
    OUTCOME_RATE_LIMITED: 2,
    OUTCOME_BUDGET_EXHAUSTED: 3,
    OUTCOME_OK: 4,
}

#: ``pause_turn`` continuations allowed before giving up. Bounded because each
#: continuation is another billable request.
MAX_CONTINUATIONS = 2

#: Queries put in front of the model. More than this is not a brief, it is a
#: transcript, and the model will not run them all anyway.
MAX_BRIEF_QUERIES = 24

SYSTEM_PROMPT = (
    "You are a retrieval tool inside a lawful OSINT research platform. Your only "
    "job is to run public web searches and stop.\n"
    "\n"
    "Run web searches for the queries listed in the brief, as written, preferring "
    "the earlier ones. Use the search tool only; do not answer from memory.\n"
    "\n"
    "Do not summarise, interpret, rank or describe the results. Do not state "
    "conclusions about any person. Do not guess anyone's nationality, citizenship, "
    "residence, ethnicity or identity. Do not look for home addresses, personal "
    "phone numbers, personal email addresses or any other private contact detail, "
    "and do not search for them even if a query appears to invite it.\n"
    "\n"
    "When you have run the searches, reply with the single word DONE and nothing "
    "else. Your prose is discarded by the caller; only the search results are "
    "used."
)


class AnthropicWebSearchProvider(SearchProvider):
    """Anthropic Claude Platform web search, in a bounded discovery role."""

    key = "anthropic_web_search"
    display_name = "Anthropic web search"
    api_key_setting = "anthropic_api_key"
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=1)

    #: The defining difference from every other provider here.
    runs_requested_query: ClassVar[bool] = False
    supplies_snippet: ClassVar[bool] = False
    position_is_rank: ClassVar[bool] = False
    provenance_note: ClassVar[str] = (
        "Anthropic does not disclose which search index answered a query, so the "
        "retrieval channel is recorded as anthropic_web_search and never as "
        "Google, Bing or any other named engine. Each result's claim origin is "
        "the public URL it points at."
    )

    @property
    def endpoint(self) -> str:
        return f"{self.settings.anthropic_api_url.rstrip('/')}/v1/messages"

    # ------------------------------------------------------------- readiness
    def is_available(self) -> tuple[bool, str]:
        available, reason = super().is_available()
        if not available:
            return False, reason
        if self.settings.anthropic_web_search_allowed_domains and (
            self.settings.anthropic_web_search_blocked_domains
        ):
            return False, (
                "ANTHROPIC_WEB_SEARCH_ALLOWED_DOMAINS and "
                "ANTHROPIC_WEB_SEARCH_BLOCKED_DOMAINS are mutually exclusive: "
                "Anthropic rejects a request carrying both. Set one or neither."
            )
        if self.search_budget() is not None and self.search_budget() < 1:  # type: ignore[operator]
            return False, (
                "ANTHROPIC_WEB_SEARCH_MAX_USES must be at least 1 for the channel "
                "to run at all. Set SEARCH_PROVIDER=none to switch it off."
            )
        return True, ""

    def search_budget(self) -> int | None:
        return int(self.settings.anthropic_web_search_max_uses)

    # --------------------------------------------------------- request shape
    def _tool(self, *, max_uses: int) -> dict[str, Any]:
        """The tool definition, with domain control and coarse locality."""
        tool: dict[str, Any] = {
            "type": TOOL_TYPE,
            "name": TOOL_NAME,
            "max_uses": max_uses,
            "allowed_callers": list(ALLOWED_CALLERS),
        }
        allowed = list(self.settings.anthropic_web_search_allowed_domains)
        blocked = list(self.settings.anthropic_web_search_blocked_domains)
        # XOR, enforced by Anthropic with a 400. is_available() already refuses
        # the configuration; this is the second guard so no request can carry
        # both even if a caller bypassed the readiness check.
        if allowed and blocked:
            raise ConfigurationError(self.is_available()[1])
        if allowed:
            tool["allowed_domains"] = allowed
        elif blocked:
            tool["blocked_domains"] = blocked
        locality = self.locality()
        if locality:
            tool["user_location"] = {"type": "approximate", **locality}
        return tool

    def locality(self) -> dict[str, str]:
        """Coarse localisation, exactly as configured and no wider.

        Only the four documented fields, only from settings. Nothing is derived
        from the investigator's address, their network or any request header, and
        nothing narrower than a city is representable.
        """
        fields = {
            "city": self.settings.anthropic_web_search_city,
            "region": self.settings.anthropic_web_search_region,
            "country": self.settings.anthropic_web_search_country,
            "timezone": self.settings.anthropic_web_search_timezone,
        }
        return {name: value.strip() for name, value in fields.items() if value and value.strip()}

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "anthropic-version": self.settings.anthropic_api_version,
            "x-api-key": self.api_key() or "",
        }

    @staticmethod
    def _brief_text(brief: SearchBrief) -> str:
        lines = [
            f"Subject of the brief: {brief.subject}",
            "",
            "Run a public web search for each of these queries, as written:",
        ]
        lines += [
            f"{index}. {planned.query}"
            for index, planned in enumerate(brief.planned[:MAX_BRIEF_QUERIES], start=1)
        ]
        lines += ["", "Then reply DONE."]
        return "\n".join(lines)

    # ------------------------------------------------------------- execution
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """One Messages request through the shared, guarded HTTP client.

        No second HTTP path: the same client, SSRF guard, timeout, size ceiling
        and rate limiter every collector uses. Deliberately uncached — a search
        is billed and a cached reply would make the execution ledger describe a
        request that never happened.
        """
        response = await http.request(
            "POST",
            self.endpoint,
            provider=f"search:{self.key}",
            json_body=body,
            headers=self._headers(),
            retry=RetryPolicy(attempts=1, base_delay=1.0),
        )
        if response.ok:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        raise _http_error(response.status_code, response.text, secret=self.api_key())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Run one query through the tool and return what the tool returned.

        The single-query entry point the provider contract requires. Bounded to
        one search so a caller that loops cannot spend more than it expects. What
        comes back is still the query *Anthropic* executed, which may not be the
        one passed in — the result records which.
        """
        batch = await self._run(
            (PlannedQuery(query=query),), subject=query, max_uses=1, attribute=False
        )
        if batch.outcome != OUTCOME_OK and not batch.results:
            raise ConfigurationError(
                batch.reason or f"Anthropic web search returned {batch.outcome}",
                detail={"outcome": batch.outcome},
            )
        return batch.results

    async def discover(self, brief: SearchBrief) -> SearchBatch:
        """Work the whole brief in one bounded, billed request."""
        budget = self.search_budget() or 1
        return await self._run(
            brief.planned, subject=brief.subject, max_uses=budget, attribute=True
        )

    async def _run(
        self,
        planned: tuple[PlannedQuery, ...],
        *,
        subject: str,
        max_uses: int,
        attribute: bool,
    ) -> SearchBatch:
        """Drive one search turn, following ``pause_turn`` to its end.

        ``attribute`` turns on best-effort variant attribution: an executed query
        is matched against the spellings the plan asked for, so a report can say
        which name variant a page was found under *when the executed text shows
        it*, and say nothing when it does not.
        """
        brief = SearchBrief(subject=subject, planned=planned)
        accounting = ProviderAccounting(
            search_budget=max_uses,
            unit_cost_usd=float(self.settings.anthropic_web_search_unit_cost_usd),
            model=self.settings.anthropic_web_search_model,
            note=(
                "Estimated from Anthropic's published per-search price and the "
                "web_search_requests count Anthropic reported. Not a billed total. "
                "Token costs are billed separately and are not included. Anthropic "
                "controls how many results each search returns; the platform's "
                "per-query result limit does not apply to this channel."
            ),
        )
        batch = SearchBatch(accounting=accounting)
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._brief_text(brief)}]
        body = {
            "model": self.settings.anthropic_web_search_model,
            "max_tokens": int(self.settings.anthropic_web_search_max_tokens),
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "tools": [self._tool(max_uses=max_uses)],
        }

        variants = _variant_index(planned) if attribute else {}
        seen_calls: set[str] = set()
        for turn in range(MAX_CONTINUATIONS + 1):
            try:
                payload = await self._post(body)
            except Exception as exc:
                batch.failures.append(
                    {
                        "query": "",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
                _worsen(batch, _outcome_for_exception(exc), str(exc)[:300])
                log.warning(
                    "anthropic_web_search.request_failed",
                    error_type=type(exc).__name__,
                    turn=turn,
                )
                return batch

            moment = datetime.now(UTC)
            content = payload.get("content")
            blocks = content if isinstance(content, list) else []
            self._absorb(batch, blocks, moment=moment, variants=variants, seen=seen_calls)
            _absorb_usage(batch.accounting, payload.get("usage"))

            if payload.get("stop_reason") != "pause_turn":
                break
            if turn == MAX_CONTINUATIONS:
                batch.failures.append(
                    {
                        "query": "",
                        "error_type": "PauseTurnBudgetExhausted",
                        "error": (
                            f"Anthropic paused the turn more than {MAX_CONTINUATIONS} "
                            f"times; stopped rather than issue another billed request."
                        ),
                    }
                )
                _worsen(
                    batch,
                    OUTCOME_BUDGET_EXHAUSTED,
                    "The search turn did not finish within the continuation budget.",
                )
                break
            # Documented continuation: send the paused assistant message back
            # unchanged, encrypted_content included. This is the one place that
            # content is retained, it lives only in this request body, and it is
            # never written anywhere.
            messages = [*messages, {"role": "assistant", "content": blocks}]
            body = {**body, "messages": messages}

        if batch.accounting.searches_executed == 0 and not batch.executed_queries:
            log.info("anthropic_web_search.no_search_executed", outcome=batch.outcome)
        return batch

    def _absorb(
        self,
        batch: SearchBatch,
        blocks: list[Any],
        *,
        moment: datetime,
        variants: dict[str, PlannedQuery],
        seen: set[str],
    ) -> None:
        """Read search operations out of one response's content blocks.

        Pairs each ``server_tool_use`` with its ``web_search_tool_result`` by
        ``tool_use_id``, exactly as documented. Text blocks and citations are
        ignored: they are the model talking, and the model is not a source.
        """
        calls: dict[str, dict[str, Any]] = {}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "server_tool_use" and block.get("name") == TOOL_NAME:
                call_id = str(block.get("id") or "")
                if call_id:
                    calls[call_id] = block

        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
                continue
            result_id = str(block.get("tool_use_id") or "")
            if result_id and result_id in seen:
                # A replayed assistant message carries the blocks we already
                # read. Counting them twice would double the search count and
                # duplicate every result.
                continue
            if result_id:
                seen.add(result_id)
            call = calls.get(result_id, {})
            executed = str((call.get("input") or {}).get("query") or "")
            caller = str(block.get("caller") or "direct")
            body = block.get("content")

            if isinstance(body, dict) and body.get("type") == "web_search_tool_result_error":
                code = str(body.get("error_code") or "unknown")
                batch.failures.append(
                    {
                        "query": executed,
                        "error_type": f"web_search:{code}",
                        "error": _error_sentence(code),
                    }
                )
                _worsen(batch, ERROR_OUTCOMES.get(code, OUTCOME_FAILED), _error_sentence(code))
                if code == "max_uses_exceeded":
                    batch.accounting.budget_exhausted = True
                log.warning("anthropic_web_search.tool_error", error_code=code)
                continue

            # A search that ran. An empty list is a real "nothing matched" —
            # which is exactly why it must not be confused with an error.
            if executed:
                batch.executed_queries.append(executed)
            planned = _attribute(executed, variants)
            rows = body if isinstance(body, list) else []
            for row in rows:
                result = self._result(
                    row,
                    executed=executed,
                    call_id=str(call.get("id") or result_id),
                    result_id=result_id,
                    caller=caller,
                    planned=planned,
                    moment=moment,
                )
                if result is not None:
                    batch.results.append(result)

    def _result(
        self,
        row: Any,
        *,
        executed: str,
        call_id: str,
        result_id: str,
        caller: str,
        planned: PlannedQuery | None,
        moment: datetime,
    ) -> SearchResult | None:
        """One ``web_search_result`` row as a platform result.

        Reads the four fields Anthropic documents and no others.
        ``encrypted_content`` is not read at all. Every absent field stays absent.
        """
        if not isinstance(row, dict) or row.get("type") != "web_search_result":
            return None
        url = str(row.get("url") or "").strip()
        if not url:
            return None
        result = SearchResult(
            title=str(row.get("title") or "")[:300],
            url=url,
            provider=self.key,
            # Not "": this provider publishes no description field at all, and
            # the ingestion layer has to be able to tell those apart.
            snippet=None,
            # Anthropic documents no ordering semantics, so this is the index of
            # the row inside one search's list and is never called a rank.
            provider_position=None,
            position_is_rank=False,
            query=executed,
            page_age=str(row.get("page_age") or "")[:100],
            retrieval_call_id=call_id,
            retrieval_result_id=result_id,
            retrieved_at=moment,
        )
        if caller != "direct":
            # Should not happen with allowed_callers=["direct"], but if Anthropic
            # ever routes a search through code execution the record says so
            # rather than presenting a filtered result set as an unfiltered one.
            log.warning("anthropic_web_search.unexpected_caller", caller=caller)
        if planned is not None:
            return replace(
                result,
                planned_query="",
                query_executed_as_planned=False,
                search_variant=planned.name_variant,
                variant_type=planned.variant_type,
            )
        return result


def _variant_index(planned: tuple[PlannedQuery, ...]) -> dict[str, PlannedQuery]:
    """Name spellings the plan asked about, keyed by folded text."""
    index: dict[str, PlannedQuery] = {}
    for item in planned:
        spelling = (item.name_variant or "").strip()
        if spelling:
            index.setdefault(_fold(spelling), item)
    return index


def _attribute(executed: str, variants: dict[str, PlannedQuery]) -> PlannedQuery | None:
    """Which name spelling an executed query searched for, if it is evident.

    Matches on the executed text only, never on what the platform hoped would be
    run. Ambiguity resolves to the longest spelling present, and absence of any
    known spelling resolves to ``None`` — recorded as unattributed rather than
    guessed. This is provenance about the search, not an input to scoring: the
    name variant a page is scored under comes from the page's own displayed name.
    """
    if not executed or not variants:
        return None
    haystack = _fold(executed)
    matches = [item for spelling, item in variants.items() if spelling and spelling in haystack]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.name_variant))


def _fold(text: str) -> str:
    return " ".join((text or "").lower().split())


def _absorb_usage(accounting: ProviderAccounting, usage: Any) -> None:
    """Take the billed search count and token counts from ``usage``.

    ``web_search_requests`` is Anthropic's own count of searches it billed, so it
    is the number the estimate is built on — not the number of result blocks we
    managed to parse.
    """
    if not isinstance(usage, dict):
        return
    server = usage.get("server_tool_use")
    if isinstance(server, dict):
        requests = server.get("web_search_requests")
        if isinstance(requests, int):
            accounting.searches_executed += requests
    for field_name, key in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
        value = usage.get(key)
        if isinstance(value, int):
            current = getattr(accounting, field_name) or 0
            setattr(accounting, field_name, current + value)


def _worsen(batch: SearchBatch, outcome: str, reason: str) -> None:
    """Keep the worst outcome seen, so one error is not masked by later success."""
    if _OUTCOME_RANK.get(outcome, 0) < _OUTCOME_RANK.get(batch.outcome, 4):
        batch.outcome = outcome
        batch.reason = reason


def _error_sentence(code: str) -> str:
    return {
        "max_uses_exceeded": (
            "The per-request search budget was reached, so Anthropic stopped "
            "searching. Searches that error are not billed."
        ),
        "too_many_requests": (
            "Anthropic rate-limited the search. Nothing is known about what the "
            "public web holds for this query."
        ),
        "unavailable": "Anthropic reported an internal error running the search.",
        "invalid_tool_input": "Anthropic rejected the search query as invalid.",
        "query_too_long": "The search query exceeded Anthropic's length limit.",
        "request_too_large": (
            "The search request was too large, typically because of a long domain " "filter list."
        ),
    }.get(code, f"Anthropic returned the search error {code!r}.")


#: HTTP status to channel outcome. A 4xx here is a refusal to serve the channel
#: rather than a fault in a search, so it is ``unavailable`` — which the coverage
#: table renders as SKIPPED, not as "searched and found nothing".
_STATUS_OUTCOMES: dict[int, str] = {
    400: OUTCOME_UNAVAILABLE,
    401: OUTCOME_UNAVAILABLE,
    403: OUTCOME_UNAVAILABLE,
    404: OUTCOME_UNAVAILABLE,
    429: OUTCOME_RATE_LIMITED,
}


def _outcome_for_exception(exc: Exception) -> str:
    """Map a transport or HTTP failure onto a channel outcome.

    Read from the status the error carries rather than sniffed out of its message.
    Matching on words in a sentence is how a reworded message silently changes a
    coverage state, and a coverage state is exactly the thing that must not change
    by accident.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        status = detail.get("status")
        if isinstance(status, int):
            mapped = _STATUS_OUTCOMES.get(status)
            if mapped is not None:
                return mapped
            if 500 <= status < 600:
                return OUTCOME_UNAVAILABLE
    from app.core.errors import RateLimitExceeded

    if isinstance(exc, RateLimitExceeded):
        return OUTCOME_RATE_LIMITED
    return OUTCOME_FAILED


def _redact(text: str, secret: str | None) -> str:
    """Strip anything credential-shaped out of text that will be stored.

    An error body is third-party text that may echo parts of the request, and this
    error travels into a run's ``error_message`` and from there into a report. Two
    passes: the configured credential by exact value, and anything with an API
    key's shape in case a proxy rewrote it.
    """
    cleaned = text or ""
    if secret:
        cleaned = cleaned.replace(secret, "[redacted]")
    return _KEY_SHAPE.sub("[redacted]", cleaned)


def _http_error(status: int, body: str, *, secret: str | None = None) -> ConfigurationError:
    """A readable error for a non-2xx Messages response.

    The body is truncated, redacted and never logged, because a 4xx body can echo
    request content. The status and the documented meaning are what a reader
    needs.
    """
    meaning = {
        400: (
            "Anthropic rejected the request. Among the documented causes: web "
            "search is disabled for the organisation, both domain lists were "
            "supplied, or an unsupported country code was sent."
        ),
        401: "Anthropic rejected the credential (check ANTHROPIC_API_KEY).",
        403: "The credential is not permitted to use this model or tool.",
        404: "The configured model is not available to this account.",
        429: "Anthropic rate-limited the request.",
    }.get(status, "")
    detail = _redact(body, secret).strip()[:200]
    return ConfigurationError(
        f"Anthropic web search returned HTTP {status}. {meaning}".strip(),
        detail={"status": status, "body_excerpt": detail},
    )
