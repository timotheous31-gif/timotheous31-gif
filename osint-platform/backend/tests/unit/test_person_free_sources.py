"""The free, key-free PERSON collectors.

Each source gets the same three questions: does it translate its own API's shape
correctly, does it keep same-name records apart, and does it stay honest about
what it does not know. The shared assessment logic — how supplied context raises
or fails to raise a candidate — is covered once, at the bottom.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.crossref import CrossrefCollector
from app.collectors.github_people import GitHubPeopleCollector
from app.collectors.openalex import OpenAlexCollector
from app.collectors.orcid import OrcidCollector
from app.collectors.person import (
    MAX_CANDIDATES,
    PersonCandidate,
    PersonContext,
    assess,
)
from app.collectors.person_usernames import PersonUsernameCollector
from app.collectors.reddit import RedditCollector
from app.collectors.wikidata import WikidataCollector
from app.core.errors import CollectorError, CollectorUnavailable
from app.core.settings import Settings
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import normalize_target

NAME = "Timotheous Samar"


def settings(**overrides) -> Settings:
    """Settings with no credentials, so "free" is what is actually tested."""
    absent = {
        "github_token": None,
        "hibp_api_key": None,
        "brave_api_key": None,
        "bing_api_key": None,
        "serper_api_key": None,
    }
    return Settings(_env_file=None, **{**absent, **overrides})


def person(context: dict | None = None):
    target = normalize_target(NAME, TargetType.PERSON)
    if context:
        return type(target)(
            type=target.type,
            raw_input=target.raw_input,
            value=target.value,
            attributes={**target.attributes, "context": context},
        )
    return target


async def run(collector, target=None, ctx=None):
    import uuid as _uuid

    from app.collectors.base import CollectorContext

    return await collector.collect(
        target or person(), ctx or CollectorContext(case_id=_uuid.uuid4())
    )


# --------------------------------------------------------------------- ORCID


ORCID_BODY = {
    "num-found": 2,
    "expanded-result": [
        {
            "orcid-id": "0000-0002-1825-0097",
            "given-names": "Timotheous",
            "family-names": "Samar",
            "institution-name": ["Example University"],
            "works-count": 12,
        },
        {
            "orcid-id": "0000-0001-5109-3700",
            "given-names": "Timotheous",
            "family-names": "Samar",
            "institution-name": ["Example Institute of Marine Biology"],
            "works-count": 3,
        },
    ],
}


@respx.mock
async def test_orcid_keeps_two_researchers_of_one_name_apart(mock_http):
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(200, json=ORCID_BODY)
    )
    result = await run(OrcidCollector(settings()))

    assert len(result.findings) == 2
    urls = {finding.data["url"] for finding in result.findings}
    assert urls == {
        "https://orcid.org/0000-0002-1825-0097",
        "https://orcid.org/0000-0001-5109-3700",
    }
    for finding in result.findings:
        assert finding.kind is FindingKind.PERSON_CANDIDATE
        assert finding.classification is Classification.PERSONAL
        # Name only: below the POSSIBLE_MATCH band.
        assert finding.confidence <= 0.30


@respx.mock
async def test_orcid_records_affiliations_for_later_corroboration(mock_http):
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(200, json=ORCID_BODY)
    )
    result = await run(OrcidCollector(settings()))
    first = next(f for f in result.findings if "1825" in f.data["url"])
    assert first.data["affiliations"] == ["Example University"]
    assert first.data["identifiers"]["orcid"] == "0000-0002-1825-0097"


@respx.mock
async def test_orcid_needs_no_credential(mock_http):
    route = respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(200, json={"expanded-result": []})
    )
    collector = OrcidCollector(settings())
    assert collector.is_available() == (True, "")
    assert collector.configuration().required_settings == []
    await run(collector)
    assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}


@respx.mock
async def test_orcid_reports_a_failure_rather_than_an_empty_result(mock_http):
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(return_value=httpx.Response(503))
    with pytest.raises(CollectorError, match="503"):
        await run(OrcidCollector(settings()))


# ------------------------------------------------------------------ OpenAlex


OPENALEX_BODY = {
    "meta": {"count": 1},
    "results": [
        {
            "id": "https://openalex.org/A5023888391",
            "display_name": "Timotheous Samar",
            "orcid": "https://orcid.org/0000-0002-1825-0097",
            "works_count": 12,
            "cited_by_count": 340,
            "last_known_institutions": [
                {"display_name": "Example University", "country_code": "NL"}
            ],
        }
    ],
}


@respx.mock
async def test_openalex_translates_an_author_record(mock_http):
    respx.get("https://api.openalex.org/authors").mock(
        return_value=httpx.Response(200, json=OPENALEX_BODY)
    )
    result = await run(OpenAlexCollector(settings()))

    assert len(result.findings) == 1
    data = result.findings[0].data
    assert data["url"] == "https://openalex.org/A5023888391"
    assert data["affiliations"] == ["Example University"]
    assert data["locations"] == ["NL"]
    assert data["identifiers"] == {
        "openalex": "https://openalex.org/A5023888391",
        "orcid": "0000-0002-1825-0097",
    }


@respx.mock
async def test_openalex_sends_no_key_and_no_mailto_by_default(mock_http):
    route = respx.get("https://api.openalex.org/authors").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await run(OpenAlexCollector(settings()))
    assert "mailto" not in route.calls[0].request.url.params
    assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}


# ------------------------------------------------------------------ Crossref


CROSSREF_BODY = {
    "message": {
        "items": [
            {
                "DOI": "10.5555/example.1",
                "title": ["A paper about examples"],
                "container-title": ["Journal of Examples"],
                "issued": {"date-parts": [[2021, 3]]},
                "author": [
                    {
                        "given": "Timotheous",
                        "family": "Samar",
                        "affiliation": [{"name": "Example University"}],
                    },
                    {"given": "Other", "family": "Person", "affiliation": []},
                ],
            },
            {
                "DOI": "10.5555/example.2",
                "title": ["A paper by somebody else entirely"],
                "author": [{"given": "Unrelated", "family": "Author"}],
            },
        ]
    }
}


@respx.mock
async def test_crossref_only_records_works_that_credit_the_searched_name(mock_http):
    """Crossref's author query is fuzzy; unrelated authors must not become
    candidates attributed to the subject."""
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=CROSSREF_BODY)
    )
    result = await run(CrossrefCollector(settings()))

    assert len(result.findings) == 1
    data = result.findings[0].data
    assert data["url"] == "https://doi.org/10.5555/example.1"
    assert data["affiliations"] == ["Example University"]
    assert data["co_author_count"] == 1


@respx.mock
async def test_crossref_says_so_when_nothing_credits_the_name(mock_http):
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"items": [{"DOI": "10.5555/x", "author": [{"family": "Nobody"}]}]}},
        )
    )
    result = await run(CrossrefCollector(settings()))
    assert result.findings == []
    assert any("none credits an author" in note for note in result.notes)


# ------------------------------------------------------------------ Wikidata


def _wikidata_routes(entities: dict, labels: dict | None = None):
    """Wikidata answers three calls on one URL; dispatch on the action."""

    def handler(request):
        action = request.url.params.get("action")
        if action == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": qid} for qid in entities]})
        if action == "wbgetentities" and request.url.params.get("props") == "labels":
            return httpx.Response(200, json={"entities": labels or {}})
        return httpx.Response(200, json={"entities": entities})

    return respx.get("https://www.wikidata.org/w/api.php").mock(side_effect=handler)


def _human(label: str, description: str, employer: str | None = None, article: str | None = None):
    entity = {
        "labels": {"en": {"value": label}},
        "descriptions": {"en": {"value": description}},
        "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
        "sitelinks": {},
    }
    if employer:
        entity["claims"]["P108"] = [{"mainsnak": {"datavalue": {"value": {"id": employer}}}}]
    if article:
        entity["sitelinks"] = {"enwiki": {"url": article}}
    return entity


@respx.mock
async def test_wikidata_drops_items_that_are_not_people(mock_http):
    """A name search matches ships and songs; those are not person candidates."""
    _wikidata_routes(
        {
            "Q111": _human("Timotheous Samar", "researcher"),
            "Q222": {
                "labels": {"en": {"value": "Timotheous Samar"}},
                "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11446"}}}}]},
            },
        }
    )
    result = await run(WikidataCollector(settings()))

    assert len(result.findings) == 1
    assert result.findings[0].data["url"] == "https://www.wikidata.org/wiki/Q111"
    assert any("are not people" in note for note in result.notes)


@respx.mock
async def test_wikidata_resolves_claim_ids_to_readable_labels(mock_http):
    _wikidata_routes(
        {
            "Q111": _human(
                "Timotheous Samar",
                "researcher",
                employer="Q900",
                article="https://en.wikipedia.org/wiki/X",
            )
        },
        labels={"Q900": {"labels": {"en": {"value": "Example University"}}}},
    )
    result = await run(WikidataCollector(settings()))
    data = result.findings[0].data
    assert data["affiliations"] == ["Example University"]
    assert data["wikipedia_url"] == "https://en.wikipedia.org/wiki/X"


# -------------------------------------------------------------------- GitHub


GITHUB_SEARCH = {
    "total_count": 2,
    "items": [
        {"login": "tsamar", "html_url": "https://github.com/tsamar"},
        {"login": "other-tsamar", "html_url": "https://github.com/other-tsamar"},
    ],
}


@respx.mock
async def test_github_people_uses_search_not_a_guessed_login(mock_http):
    """The original bug was inventing a login from a name; this asks GitHub."""
    search = respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(200, json=GITHUB_SEARCH)
    )
    respx.get(url__regex=r"https://api\.github\.com/users/.*").mock(
        return_value=httpx.Response(
            200, json={"name": "Timotheous Samar", "company": "Example Ltd"}
        )
    )
    result = await run(GitHubPeopleCollector(settings()))

    query = search.calls[0].request.url.params["q"]
    assert "in:fullname" in query
    assert NAME in query
    # Never a bare login built by deleting the spaces from the name.
    assert "timotheoussamar" not in query.lower().replace('"', "")
    assert len(result.findings) == 2


@respx.mock
async def test_github_people_sends_no_authorization_without_a_token(mock_http):
    route = respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    await run(GitHubPeopleCollector(settings()))
    assert "authorization" not in {k.lower() for k in route.calls[0].request.headers}


@respx.mock
async def test_github_people_reports_a_rate_limit_as_skippable(mock_http):
    respx.get("https://api.github.com/search/users").mock(return_value=httpx.Response(403))
    with pytest.raises(CollectorUnavailable, match="GITHUB_TOKEN"):
        await run(GitHubPeopleCollector(settings()))


# -------------------------------------------------------------------- Reddit


@respx.mock
async def test_reddit_translates_public_user_results(mock_http):
    respx.get("https://www.reddit.com/search.json").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"children": [{"data": {"name": "example_user", "total_karma": 42}}]}},
        )
    )
    result = await run(RedditCollector(settings()))
    data = result.findings[0].data
    assert data["url"] == "https://www.reddit.com/user/example_user"
    assert data["handles"] == ["example_user"]
    assert data["name_evidence"] == "handle_only"


@pytest.mark.parametrize("status", [401, 403, 429])
@respx.mock
async def test_reddit_blocking_anonymous_access_is_skipped_not_failed(mock_http, status):
    """Reddit refusing anonymous callers is a deployment fact, not a bug — and
    must not read as "no results"."""
    respx.get("https://www.reddit.com/search.json").mock(return_value=httpx.Response(status))
    with pytest.raises(CollectorUnavailable, match="refused"):
        await run(RedditCollector(settings()))


# ------------------------------------------------ supplied-username profiles


@respx.mock
async def test_supplied_usernames_are_checked_and_guessed_ones_are_not(mock_http):
    routes = respx.get(url__regex=r"https://.*").mock(return_value=httpx.Response(404))
    result = await run(
        PersonUsernameCollector(settings()),
        person({"known_usernames": ["examplehandle"]}),
    )

    probed = " ".join(str(call.request.url) for call in routes.calls)
    assert "examplehandle" in probed
    # The name must never be turned into a handle and probed.
    assert "timotheoussamar" not in probed.lower()
    assert result.stats["supplied_usernames"] == 1


async def test_no_supplied_usernames_means_no_probing_at_all(mock_http):
    result = await run(PersonUsernameCollector(settings()), person())
    assert result.findings == []
    assert any("No known usernames were supplied" in note for note in result.notes)


# ------------------------------------------------------- shared assessment


def _candidate(**kwargs) -> PersonCandidate:
    base = {"url": "https://example.com/a", "name": NAME}
    return PersonCandidate(**{**base, **kwargs})


def test_a_name_alone_never_reaches_the_possible_match_band():
    assessment = assess(_candidate(), NAME, PersonContext())
    from app.correlation.confidence import default_engine

    assert default_engine.score(assessment.signals).score <= 0.30
    assert assessment.corroborated_by == []


def test_supplied_affiliation_corroborates_a_candidate():
    context = PersonContext(organizations=("Example University",))
    assessment = assess(
        _candidate(affiliations=["Example University, Dept of Examples"]), NAME, context
    )
    assert "affiliation" in assessment.corroborated_by
    assert any("Example University" in reason for reason in assessment.match_reasons)


def test_an_unmatched_affiliation_is_reported_as_a_reason_against():
    context = PersonContext(organizations=("Example University",))
    assessment = assess(_candidate(affiliations=["Unrelated Institute"]), NAME, context)
    assert assessment.corroborated_by == []
    assert any(
        "do not match" in r or "match the ones you supplied" in r
        for r in assessment.mismatch_reasons
    )


def test_supplied_username_corroborates_a_candidate():
    context = PersonContext(known_usernames=("ExampleHandle",))
    assessment = assess(_candidate(handles=["examplehandle"]), NAME, context)
    assert "username" in assessment.corroborated_by


def test_supplied_profile_url_is_the_strongest_corroboration():
    context = PersonContext(profile_urls=("https://example.com/a/",))
    assessment = assess(_candidate(), NAME, context)
    assert "profile_url" in assessment.corroborated_by


def test_corroboration_never_reaches_the_auto_merge_threshold():
    """Even everything matching at once leaves the candidate a candidate."""
    from app.correlation.confidence import AUTO_MERGE_THRESHOLD, default_engine

    context = PersonContext(
        known_usernames=("handle",),
        profile_urls=("https://example.com/a",),
        organizations=("Example University",),
        city="Delft",
    )
    assessment = assess(
        _candidate(
            handles=["handle"],
            affiliations=["Example University"],
            locations=["Delft, Netherlands"],
        ),
        NAME,
        context,
    )
    assert len(assessment.corroborated_by) == 4
    assert default_engine.score(assessment.signals).score < AUTO_MERGE_THRESHOLD


def test_no_context_is_stated_as_a_limit_on_every_candidate():
    assessment = assess(_candidate(), NAME, PersonContext())
    assert any("No context was supplied" in reason for reason in assessment.mismatch_reasons)


def test_a_differently_spelled_name_is_flagged_as_a_reason_against():
    assessment = assess(_candidate(name="Timotheous Samari"), NAME, PersonContext())
    assert any("spelling differs" in reason for reason in assessment.mismatch_reasons)


@respx.mock
async def test_candidates_are_bounded(mock_http):
    """A name search must not return an unbounded list of strangers."""
    respx.get("https://pub.orcid.org/v3.0/expanded-search/").mock(
        return_value=httpx.Response(
            200,
            json={
                "expanded-result": [
                    {
                        "orcid-id": f"0000-0000-0000-{index:04d}",
                        "given-names": "Timotheous",
                        "family-names": "Samar",
                    }
                    for index in range(MAX_CANDIDATES + 15)
                ]
            },
        )
    )
    result = await run(OrcidCollector(settings()))
    assert len(result.findings) == MAX_CANDIDATES
