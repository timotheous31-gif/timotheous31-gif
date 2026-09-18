"""Anthropic web search: every documented shape, and none of the live ones.

Nothing in this file contacts Anthropic. Every response is a fixture built to the
shapes Anthropic's web-search documentation publishes, which is the only way to
test this channel in CI: a live call costs money, needs a credential CI does not
have, and would make the suite's result depend on what the public web said today.

The tests are grouped by the thing that can go wrong:

* the channel being configured when it is not;
* a field being invented that the API never returned;
* a planned query being recorded as an executed one;
* an error arriving inside an HTTP 200 and being read as an absence;
* a credential or an opaque blob escaping into something that gets stored.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.errors import ConfigurationError
from app.core.settings import Settings
from app.services.providers.anthropic_web_search import (
    ALLOWED_CALLERS,
    TOOL_TYPE,
    AnthropicWebSearchProvider,
    _redact,
)
from app.services.providers.search import (
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_RATE_LIMITED,
    OUTCOME_UNAVAILABLE,
    PlannedQuery,
    SearchBrief,
)

pytestmark = pytest.mark.anyio

ENDPOINT = "https://api.anthropic.com/v1/messages"
KEY = "sk-ant-test-DO-NOT-USE-0000"
PROFILE = "https://example.org/people/tabitha-afzal"
TEAM = "https://example.org/team/communications"
BLOB = "EqgfCioIARgBIiQ3YTAwMjY1Mi1mZjM5LTQ1NGUtODgxNC1kNjNjNTk1ZWI3Y"


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "search_provider": "anthropic_web_search", "anthropic_api_key": KEY}
    base.update(overrides)
    return Settings(**base)


def _provider(**overrides) -> AnthropicWebSearchProvider:
    return AnthropicWebSearchProvider(_settings(**overrides))


def _row(url: str, title: str, page_age: str = "April 30, 2025") -> dict:
    """One ``web_search_result``, with every field Anthropic documents."""
    return {
        "type": "web_search_result",
        "url": url,
        "title": title,
        "page_age": page_age,
        "encrypted_content": BLOB,
    }


def _payload(
    searches,
    *,
    stop_reason: str = "end_turn",
    requests: int | None = None,
    start: int = 1,
) -> dict:
    """A Messages response carrying ``searches`` as (query, rows) pairs.

    ``start`` shifts the ``srvtoolu_`` ids. Real ids are unique per search, and a
    continuation turn must not reuse one, so a test that spans two turns says so
    explicitly.
    """
    content: list[dict] = [{"type": "text", "text": "I'll search for that."}]
    for index, (query, rows) in enumerate(searches, start=start):
        call_id = f"srvtoolu_{index:02d}"
        content.append(
            {
                "type": "server_tool_use",
                "id": call_id,
                "name": "web_search",
                "input": {"query": query},
            }
        )
        content.append({"type": "web_search_tool_result", "tool_use_id": call_id, "content": rows})
    content.append({"type": "text", "text": "DONE"})
    return {
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 6039,
            "output_tokens": 931,
            "server_tool_use": {
                "web_search_requests": len(searches) if requests is None else requests
            },
        },
    }


def _error_payload(code: str, query: str = "tabitha afzal") -> dict:
    """A tool-result error, which Anthropic documents as arriving inside a 200."""
    return {
        "role": "assistant",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_01",
                "name": "web_search",
                "input": {"query": query},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_01",
                "content": {"type": "web_search_tool_result_error", "error_code": code},
            },
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {}},
    }


def _brief(*queries: str) -> SearchBrief:
    return SearchBrief(
        subject="Tabitha Afzal",
        planned=tuple(
            PlannedQuery(
                query=query,
                family="general",
                name_variant="Tabitha Afzal",
                variant_type="EXACT_NAME",
            )
            for query in queries
        ),
    )


# ------------------------------------------------------------- configuration


def test_the_channel_is_unavailable_without_a_credential():
    provider = AnthropicWebSearchProvider(
        Settings(_env_file=None, search_provider="anthropic_web_search")
    )
    available, reason = provider.is_available()
    assert available is False
    assert "ANTHROPIC_API_KEY" in reason


def test_a_blank_credential_reads_as_absent_not_as_configured():
    """The empty-string-from-compose bug class, on the new credential."""
    provider = AnthropicWebSearchProvider(
        Settings(_env_file=None, search_provider="anthropic_web_search", anthropic_api_key="")
    )
    assert provider.is_available()[0] is False


def test_the_tool_definition_pins_the_basic_version_and_direct_callers():
    """The version choice is load-bearing, so it is asserted rather than trusted.

    Dynamic filtering would let model-written code discard results before the
    platform saw them, and response-inclusion control would remove the blocks
    entirely. Both are silent losses of evidence.
    """
    tool = _provider()._tool(max_uses=4)
    assert tool["type"] == TOOL_TYPE == "web_search_20250305"
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == 4
    assert tool["allowed_callers"] == list(ALLOWED_CALLERS) == ["direct"]
    assert "response_inclusion" not in tool


def test_the_search_budget_is_the_configured_ceiling():
    assert _provider(anthropic_web_search_max_uses=2).search_budget() == 2


def test_a_budget_below_one_is_refused_rather_than_silently_ignored():
    available, reason = _provider(anthropic_web_search_max_uses=0).is_available()
    assert available is False
    assert "MAX_USES" in reason


def test_domain_control_is_exclusive_because_anthropic_rejects_both():
    provider = _provider(
        anthropic_web_search_allowed_domains=["example.org"],
        anthropic_web_search_blocked_domains=["example.net"],
    )
    available, reason = provider.is_available()
    assert available is False
    assert "mutually exclusive" in reason
    with pytest.raises(ConfigurationError):
        provider._tool(max_uses=1)


def test_an_allow_list_is_sent_alone():
    provider = _provider(anthropic_web_search_allowed_domains=["example.org", "example.com/team"])
    tool = provider._tool(max_uses=1)
    assert tool["allowed_domains"] == ["example.org", "example.com/team"]
    assert "blocked_domains" not in tool


def test_a_block_list_is_sent_alone():
    tool = _provider(anthropic_web_search_blocked_domains=["example.net"])._tool(max_uses=1)
    assert tool["blocked_domains"] == ["example.net"]
    assert "allowed_domains" not in tool


def test_locality_is_coarse_and_comes_only_from_settings():
    """Nothing narrower than a city is representable, and no IP is involved."""
    provider = _provider(
        anthropic_web_search_city="Cape Town",
        anthropic_web_search_country="ZA",
        anthropic_web_search_timezone="Africa/Johannesburg",
    )
    assert provider.locality() == {
        "city": "Cape Town",
        "country": "ZA",
        "timezone": "Africa/Johannesburg",
    }
    tool = provider._tool(max_uses=1)
    assert tool["user_location"] == {"type": "approximate", **provider.locality()}
    # The only four keys the API documents, and no street, postcode or address.
    assert set(tool["user_location"]) <= {"type", "city", "region", "country", "timezone"}


def test_no_locality_means_no_user_location_block_at_all():
    assert "user_location" not in _provider()._tool(max_uses=1)


# ----------------------------------------------------------- happy-path shape


@respx.mock
async def test_a_successful_response_yields_results_with_no_invented_fields(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json=_payload([("tabitha afzal communications", [_row(PROFILE, "Tabitha Afzal")])])
        )
    )
    batch = await _provider().discover(_brief('"Tabitha Afzal"'))
    assert batch.outcome == OUTCOME_OK
    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.url == PROFILE
    assert result.title == "Tabitha Afzal"
    assert result.page_age == "April 30, 2025"
    # The four fields the API does not return stay absent. ``None`` on snippet is
    # the load-bearing one: it means "this provider publishes no description",
    # which the ingestion layer reads differently from an empty description.
    assert result.snippet is None
    assert result.provider_position is None
    assert result.position_is_rank is False
    assert result.displayed_url == ""
    assert result.result_type == ""
    assert result.image_url == ""


@respx.mock
async def test_a_result_records_the_query_anthropic_executed_not_the_one_planned(mock_http):
    """The defining property of this channel, asserted rather than documented."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload([("tabitha afzal communications officer", [_row(PROFILE, "Profile")])]),
        )
    )
    batch = await _provider().discover(_brief('"Tabitha Afzal" linkedin'))
    result = batch.results[0]
    assert result.query == "tabitha afzal communications officer"
    assert result.planned_query == ""
    assert result.query_executed_as_planned is False
    assert batch.executed_queries == ["tabitha afzal communications officer"]


@respx.mock
async def test_two_searches_in_one_response_are_paired_by_their_tool_ids(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    ("tabitha afzal", [_row(PROFILE, "Profile")]),
                    ("tabitha afzal example org", [_row(TEAM, "Team"), _row(PROFILE, "Profile")]),
                ]
            ),
        )
    )
    batch = await _provider().discover(_brief("a", "b"))
    assert batch.executed_queries == ["tabitha afzal", "tabitha afzal example org"]
    assert len(batch.results) == 3
    by_call = {result.retrieval_call_id for result in batch.results}
    assert by_call == {"srvtoolu_01", "srvtoolu_02"}
    # The result block references the call that made it, which is the pairing
    # invariant the parser relies on.
    for result in batch.results:
        assert result.retrieval_result_id == result.retrieval_call_id
    first = [r for r in batch.results if r.retrieval_call_id == "srvtoolu_01"]
    assert [r.query for r in first] == ["tabitha afzal"]


@respx.mock
async def test_an_empty_content_list_is_a_real_absence_not_an_error(mock_http):
    """Documented: a search that matched nothing returns an empty list."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("tabitha afzal", [])]))
    )
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_OK
    assert batch.results == []
    assert batch.executed_queries == ["tabitha afzal"]
    assert batch.failures == []


@respx.mock
async def test_the_models_prose_is_never_read_as_evidence(mock_http):
    """Only result blocks are read. A summary of a page is not the page."""
    payload = _payload([("tabitha afzal", [_row(PROFILE, "Profile")])])
    payload["content"].append(
        {
            "type": "text",
            "text": "Tabitha Afzal is a communications officer.",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": PROFILE,
                    "title": "Profile",
                    "encrypted_index": "Eo8BCioIAhgBIiQ",
                    "cited_text": "communications officer at Example",
                }
            ],
        }
    )
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
    batch = await _provider().discover(_brief("a"))
    assert len(batch.results) == 1
    assert batch.results[0].snippet is None
    blob = str([result.to_dict() for result in batch.results])
    assert "communications officer" not in blob


@respx.mock
async def test_encrypted_content_never_reaches_a_result_or_its_stored_form(mock_http):
    """It is opaque, it is only for replay, and it must not be persisted."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(PROFILE, "Profile")])]))
    )
    batch = await _provider().discover(_brief("a"))
    for result in batch.results:
        assert BLOB not in str(result)
        assert BLOB not in str(result.to_dict())
        assert "encrypted_content" not in result.to_dict()


# --------------------------------------------------- errors inside an HTTP 200


@respx.mock
async def test_max_uses_exceeded_is_budget_exhausted_and_never_an_absence(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_error_payload("max_uses_exceeded"))
    )
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_BUDGET_EXHAUSTED
    assert batch.accounting.budget_exhausted is True
    assert batch.executed_queries == []
    assert batch.failures and batch.failures[0]["error_type"] == "web_search:max_uses_exceeded"


@respx.mock
async def test_too_many_requests_is_rate_limited(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_error_payload("too_many_requests"))
    )
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_RATE_LIMITED
    assert "rate-limited" in batch.reason


@respx.mock
async def test_unavailable_is_unavailable(mock_http):
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_error_payload("unavailable")))
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_UNAVAILABLE


@respx.mock
@pytest.mark.parametrize("code", ["invalid_tool_input", "query_too_long", "request_too_large"])
async def test_the_remaining_documented_error_codes_are_failures(mock_http, code):
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_error_payload(code)))
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_FAILED


@respx.mock
async def test_an_unknown_error_code_is_a_failure_rather_than_being_ignored(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_error_payload("something_new_from_anthropic"))
    )
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_FAILED
    assert "something_new_from_anthropic" in batch.failures[0]["error"]


@respx.mock
async def test_one_error_among_successes_is_not_masked_by_them(mock_http):
    payload = _payload([("found", [_row(PROFILE, "Profile")])], requests=1)
    payload["content"].append(
        {
            "type": "server_tool_use",
            "id": "srvtoolu_99",
            "name": "web_search",
            "input": {"query": "too many"},
        }
    )
    payload["content"].append(
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_99",
            "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"},
        }
    )
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
    batch = await _provider().discover(_brief("a", "b"))
    assert len(batch.results) == 1
    assert batch.outcome == OUTCOME_BUDGET_EXHAUSTED


# ------------------------------------------------------------------- HTTP 4xx


@respx.mock
async def test_http_400_names_the_documented_causes(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            400, json={"type": "error", "error": {"message": "web search is not enabled"}}
        )
    )
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_UNAVAILABLE
    assert "HTTP 400" in batch.failures[0]["error"]
    assert batch.results == []


@respx.mock
async def test_http_401_is_reported_without_echoing_the_credential(mock_http):
    """An error body travels into a run record and from there into a report."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(401, text=f"invalid x-api-key: {KEY}"))
    batch = await _provider().discover(_brief("a"))
    assert batch.outcome == OUTCOME_UNAVAILABLE
    assert KEY not in str(batch.failures)
    assert KEY not in batch.reason


def test_redaction_removes_both_the_configured_key_and_anything_key_shaped():
    assert KEY not in _redact(f"body {KEY} tail", KEY)
    assert "sk-ant-unrelated-AAAABBBB" not in _redact("body sk-ant-unrelated-AAAABBBB", None)


@respx.mock
async def test_the_credential_is_sent_as_a_header_and_never_in_the_body(mock_http):
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(PROFILE, "P")])]))
    )
    await _provider().discover(_brief("a"))
    request = route.calls[0].request
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    # No beta header: the tool reference lists the beta-header column for every
    # web-search version as None.
    assert "anthropic-beta" not in request.headers
    assert KEY not in request.content.decode()


# --------------------------------------------------------------- accounting


@respx.mock
async def test_cost_is_estimated_from_the_count_anthropic_reported(mock_http):
    """Not from the number of result rows we managed to parse."""
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [("a", [_row(PROFILE, "P"), _row(TEAM, "T")]), ("b", [_row(TEAM, "T")])],
                requests=3,
            ),
        )
    )
    batch = await _provider(anthropic_web_search_max_uses=4).discover(_brief("a", "b"))
    accounting = batch.accounting
    assert accounting.searches_executed == 3
    assert accounting.search_budget == 4
    assert accounting.unit_cost_usd == 0.01
    assert accounting.estimated_search_cost_usd == 0.03
    assert accounting.input_tokens == 6039
    assert accounting.output_tokens == 931
    payload = accounting.to_dict()
    # The qualification travels with the number, so no renderer can show it as a
    # bill by omitting a footnote.
    assert payload["is_estimate"] is True
    assert payload["token_cost_included"] is False
    assert "Not a billed total" in payload["note"]


@respx.mock
async def test_a_search_that_errored_is_not_counted_as_executed(mock_http):
    """Anthropic does not bill an errored search, so neither does the estimate."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_error_payload("unavailable")))
    batch = await _provider().discover(_brief("a"))
    assert batch.accounting.searches_executed == 0
    assert batch.accounting.estimated_search_cost_usd == 0.0


# ------------------------------------------------------------- continuation


@respx.mock
async def test_a_paused_turn_is_continued_with_the_assistant_message_verbatim(mock_http):
    """Documented continuation: replay the paused message unchanged.

    ``encrypted_content`` must survive that replay or the next request 400s, which
    is exactly why it is held in memory and not stripped — and also why it is
    never written anywhere.
    """
    first = _payload([("first search", [_row(PROFILE, "Profile")])], stop_reason="pause_turn")
    second = _payload([("second search", [_row(TEAM, "Team")])], requests=1, start=9)
    route = respx.post(ENDPOINT).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    batch = await _provider().discover(_brief("a"))
    assert len(route.calls) == 2
    import json as _json

    body = _json.loads(route.calls[1].request.content)
    assert body["messages"][-1]["role"] == "assistant"
    replayed = body["messages"][-1]["content"]
    assert replayed == first["content"]
    assert any(
        row.get("encrypted_content") == BLOB
        for block in replayed
        if block.get("type") == "web_search_tool_result"
        for row in block["content"]
    )
    # Both turns' results, each counted once.
    assert {result.url for result in batch.results} == {PROFILE, TEAM}
    assert batch.accounting.searches_executed == 2


@respx.mock
async def test_a_replayed_block_is_not_ingested_twice(mock_http):
    """The continuation carries blocks already read; re-reading would duplicate."""
    first = _payload([("first search", [_row(PROFILE, "Profile")])], stop_reason="pause_turn")
    second = {**_payload([], requests=0), "content": [*first["content"]]}
    respx.post(ENDPOINT).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    batch = await _provider().discover(_brief("a"))
    assert [result.url for result in batch.results] == [PROFILE]


@respx.mock
async def test_an_endlessly_paused_turn_stops_rather_than_billing_forever(mock_http):
    paused = _payload([("q", [_row(PROFILE, "P")])], stop_reason="pause_turn")
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=paused))
    batch = await _provider().discover(_brief("a"))
    assert len(route.calls) == 3
    assert batch.outcome == OUTCOME_BUDGET_EXHAUSTED
    assert any(item["error_type"] == "PauseTurnBudgetExhausted" for item in batch.failures)


# --------------------------------------------------- variant attribution


@respx.mock
async def test_a_variant_is_attributed_only_when_the_executed_text_shows_it(mock_http):
    """Provenance about the search, never an input to scoring.

    The name variant a page is *scored* under comes from the page's own displayed
    name. This field says which spelling the provider actually typed, and says
    nothing when the executed query does not contain one.
    """
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                [
                    ("tabitha afzal communications", [_row(PROFILE, "P")]),
                    ("example org staff directory", [_row(TEAM, "T")]),
                ]
            ),
        )
    )
    batch = await _provider().discover(_brief("a", "b"))
    attributed = {result.url: result.search_variant for result in batch.results}
    assert attributed[PROFILE] == "Tabitha Afzal"
    assert attributed[TEAM] == ""


# ------------------------------------------------------- single-query entry


@respx.mock
async def test_the_single_query_entry_point_runs_exactly_one_search(mock_http):
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("tabitha afzal", [_row(PROFILE, "P")])]))
    )
    results = await _provider().search('"Tabitha Afzal"')
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body["tools"][0]["max_uses"] == 1
    assert len(results) == 1
    assert results[0].query == "tabitha afzal"


@respx.mock
async def test_the_single_query_entry_point_raises_when_the_channel_is_blocked(mock_http):
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_error_payload("too_many_requests"))
    )
    with pytest.raises(ConfigurationError):
        await _provider().search('"Tabitha Afzal"')


# --------------------------------------------------- what leaves the platform


@respx.mock
async def test_the_request_carries_only_the_brief_and_the_policy_prompt(mock_http):
    """Enabling this channel sends data to Anthropic, so what it sends is asserted.

    The brief is the subject's spellings and the anchors the investigator supplied,
    because that is what the queries are built from. Everything else about the case
    stays here: no notes, no analyst decisions, no findings, no evidence, no images,
    no other target, and no investigator identity.
    """
    import json as _json

    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(PROFILE, "P")])]))
    )
    brief = SearchBrief(
        subject="Tabitha Afzal",
        planned=(
            PlannedQuery(query='"Tabitha Afzal" "Example International Organization"'),
            PlannedQuery(query='"Tabitha Afzal" linkedin'),
        ),
    )
    await _provider().discover(brief)
    body = _json.loads(route.calls[0].request.content)

    assert list(body) == ["model", "max_tokens", "system", "messages", "tools"]
    assert body["messages"][0]["role"] == "user"
    sent = body["messages"][0]["content"]
    assert "Tabitha Afzal" in sent
    assert '"Tabitha Afzal" linkedin' in sent
    # The prompt is a policy surface, and it travels with every request.
    system = body["system"].lower()
    for forbidden in ("home address", "personal phone", "nationality", "citizenship"):
        assert forbidden in system
    assert "do not summarise" in system


@respx.mock
async def test_nothing_about_the_case_beyond_the_brief_is_sent(mock_http):
    """A guard against the brief quietly growing into a case export."""

    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload([("q", [_row(PROFILE, "P")])]))
    )
    await _provider().discover(_brief('"Tabitha Afzal"'))
    raw = route.calls[0].request.content.decode().lower()
    for leak in (
        "analyst",
        "decision",
        "case_id",
        "finding",
        "evidence",
        "sha256",
        "image_url",
        "notes",
        "confidence",
    ):
        assert leak not in raw, f"the request body contains {leak!r}"
