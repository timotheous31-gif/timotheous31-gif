"""Social URL classification, source-aware explanations and corroboration.

Three things are being defended here:

* a URL is classified by shape, and a shape never becomes an identity claim;
* a report says which source actually produced a record, because attributing an
  ORCID registry record to "a search provider" misstates the evidence;
* two views of the same upstream data are not two sources.
"""

from __future__ import annotations

import pytest

from app.collectors.social import classify_url, platform_by_key
from app.correlation.corroboration import (
    CORROBORATING_IDENTIFIERS,
    PROVENANCE_FAMILIES,
    are_independent,
)
from app.correlation.sources import (
    SOURCE_EXPLANATIONS,
    evidence_class,
    explain_source,
    reference_signal,
)

# --------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("url", "platform", "handle"),
    [
        ("https://www.linkedin.com/in/example-person", "linkedin", "example-person"),
        ("https://linkedin.com/company/example-ltd", "linkedin", "example-ltd"),
        ("https://www.instagram.com/exampleuser/", "instagram", "exampleuser"),
        ("https://www.facebook.com/exampleuser", "facebook", "exampleuser"),
        ("https://www.facebook.com/profile.php?id=100000", "facebook", "100000"),
        ("https://www.youtube.com/@examplechannel", "youtube", "examplechannel"),
        ("https://www.youtube.com/channel/UC123", "youtube", "UC123"),
        ("https://www.snapchat.com/add/exampleuser", "snapchat", "exampleuser"),
        ("https://github.com/octocat", "github", "octocat"),
        ("https://orcid.org/0000-0002-1825-0097", "orcid", "0000-0002-1825-0097"),
        ("https://www.reddit.com/user/exampleuser", "reddit", "exampleuser"),
    ],
)
def test_public_profile_urls_are_classified(url, platform, handle):
    profile = classify_url(url)
    assert profile is not None
    assert profile.kind == "social"
    assert profile.platform == platform
    assert profile.handle == handle


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://www.linkedin.com/jobs/view/123",
        "https://www.instagram.com/p/abc123/",
        "https://www.facebook.com/groups/example",
        "https://github.com/search?q=example",
    ],
)
def test_platform_pages_that_are_not_profiles_do_not_become_people(url):
    """A video or a job listing is not a person; treating one as a candidate
    would invent an identity out of a page."""
    profile = classify_url(url)
    assert profile is not None
    assert profile.kind == "web"
    assert profile.handle is None


def test_a_publication_host_is_classified_as_a_publication():
    profile = classify_url("https://doi.org/10.5555/example.1")
    assert profile is not None
    assert profile.kind == "publication"


def test_an_unknown_host_is_classified_as_plain_web():
    profile = classify_url("https://example.org/people/1")
    assert profile is not None
    assert profile.kind == "web"
    assert profile.platform == "example.org"


@pytest.mark.parametrize("url", ["", "   ", "javascript:alert(1)", "ftp://example.com/x"])
def test_non_http_input_is_refused(url):
    assert classify_url(url) is None


def test_classification_never_claims_ownership():
    """The dataclass has no field that could carry "this belongs to X"."""
    profile = classify_url("https://github.com/octocat")
    assert profile is not None
    assert not hasattr(profile, "owner")
    assert not hasattr(profile, "subject")


@pytest.mark.parametrize("platform", ["linkedin", "instagram", "facebook", "snapchat"])
def test_login_gated_platforms_are_marked_unfetchable_with_a_reason(platform):
    """The policy is to say so, not to work around it."""
    spec = platform_by_key(platform)
    assert spec is not None
    assert spec.server_fetchable is False
    assert spec.fetch_note
    assert (
        "no attempt" in spec.fetch_note.lower() or "only the public url" in spec.fetch_note.lower()
    )


def test_url_canonicalisation_folds_case_and_trailing_slash():
    first = classify_url("https://GitHub.com/OctoCat/")
    second = classify_url("https://github.com/OctoCat")
    assert first is not None and second is not None
    assert first.url == second.url


# ------------------------------------------------------ source-aware explanations


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("github_people", "GitHub's public user search"),
        ("orcid", "ORCID's public registry"),
        ("openalex", "OpenAlex returned this public author record"),
        ("crossref", "Crossref returned a work crediting"),
        ("wikidata", "Wikidata returned this item"),
    ],
)
def test_each_source_explains_itself_in_its_own_terms(source, expected):
    """The reported bug: these were all described as a search provider."""
    assert expected in explain_source(source)


@pytest.mark.parametrize("source", ["github_people", "orcid", "openalex", "crossref", "wikidata"])
def test_structured_sources_are_never_described_as_a_search_provider(source):
    explanation = explain_source(source).lower()
    assert "search provider" not in explanation


def test_the_manual_import_explanation_names_the_engine_and_query():
    explanation = explain_source(
        "manual_search_recon", query='"Timotheous Samar" LinkedIn', engine="Google"
    )
    assert "investigator imported" in explanation.lower()
    assert "Google" in explanation
    assert '"Timotheous Samar" LinkedIn' in explanation


def test_the_paid_search_collector_still_says_it_is_a_search_provider():
    assert "search provider" in explain_source("search", query="example").lower()


def test_an_unknown_source_gets_a_truthful_fallback():
    explanation = explain_source("some_new_collector")
    assert "some_new_collector" in explanation
    assert "search provider" not in explanation.lower()


def test_the_reference_signal_keeps_the_rules_arithmetic():
    """Only the sentence changes; the score and ceiling are the rule's."""
    from app.correlation.confidence import default_engine

    rule = default_engine.rule("search_reference")
    signal = reference_signal("orcid")
    assert signal.key == rule.key
    assert signal.score == rule.score
    assert signal.ceiling == rule.ceiling
    assert signal.reason != rule.reason


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("orcid", "api_fetched"),
        ("person_usernames", "page_fetched"),
        ("manual_search_recon", "investigator_imported"),
    ],
)
def test_evidence_classes_distinguish_how_a_record_was_obtained(source, expected):
    assert evidence_class(source) == expected


def test_every_registered_source_has_an_explanation():
    from app.correlation.sources import SOURCE_EVIDENCE_CLASS

    assert set(SOURCE_EXPLANATIONS) == set(SOURCE_EVIDENCE_CLASS)


# ----------------------------------------------------------------- independence


def test_two_unrelated_sources_are_independent():
    assert are_independent("orcid", "github_people") is True


def test_a_source_never_corroborates_itself():
    assert are_independent("orcid", "orcid") is False


@pytest.mark.parametrize("family", PROVENANCE_FAMILIES)
def test_sources_sharing_upstream_data_are_not_independent(family):
    first, second = sorted(family)[:2]
    assert are_independent(first, second) is False


def test_openalex_and_crossref_are_specifically_not_independent():
    """OpenAlex ingests Crossref: agreement on a DOI is one fact, seen twice."""
    assert are_independent("openalex", "crossref") is False


def test_a_shared_name_is_not_a_corroborating_identifier():
    assert "name" not in CORROBORATING_IDENTIFIERS
    assert "display_name" not in CORROBORATING_IDENTIFIERS
