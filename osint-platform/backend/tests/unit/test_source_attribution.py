"""Explanations must name the source that actually produced the record.

PR #4 corrected the REFERENCED_BY *relationship* signal but left the WEBSITE
*entity* carrying the generic ``search_reference`` rule reason — "A search
provider returned a page referencing both endpoints" — which is what a report
reads. An ORCID registry record described that way misstates the evidence a
reviewer is being asked to weigh.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.correlation.extraction import extract
from app.models.enums import EntityType, FindingKind, RelationshipType

GENERIC = "search provider"


def _finding(source, *, kind=FindingKind.PERSON_CANDIDATE, **extra):
    data = {
        "url": f"https://{source}.example.org/record/1",
        "host": f"{source}.example.org",
        "source": source,
        "subject_value": "example person",
        "subject_name": "Example Person",
        "name": "Example Person",
        "identifiers": {},
        "affiliations": [],
        "locations": [],
        "handles": [],
        "match_reasons": [],
        "mismatch_reasons": [],
        "corroborated_by": [],
    }
    data.update(extra)
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        collector=source,
        source_url=data["url"],
        data=data,
        title="Example",
        observed_at=None,
    )


def _website_reasons(result):
    return [
        signal.reason
        for entity in result.entities.values()
        if entity.type is EntityType.WEBSITE
        for signal in entity.signals
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("orcid", "ORCID"),
        ("openalex", "OpenAlex"),
        ("crossref", "Crossref"),
        ("wikidata", "Wikidata"),
    ],
)
def test_the_page_entity_names_the_real_source(source, expected):
    reasons = _website_reasons(extract([_finding(source)]))
    assert reasons, "the page entity must carry an explanation"
    assert any(expected.lower() in reason.lower() for reason in reasons), reasons
    assert not any(GENERIC in reason.lower() for reason in reasons), reasons


@pytest.mark.parametrize("source", ["orcid", "openalex", "crossref"])
def test_the_edge_and_the_entity_agree_about_the_source(source):
    """They describe one act of retrieval and must not be able to disagree."""
    result = extract([_finding(source)])
    edge_reasons = [
        signal.reason
        for edge in result.relationships.values()
        if edge.type is RelationshipType.REFERENCED_BY
        for signal in edge.signals
    ]
    assert edge_reasons
    assert set(edge_reasons) == set(_website_reasons(result))


def test_a_real_search_result_may_still_use_search_wording():
    finding = _finding(
        "search", kind=FindingKind.SEARCH_RESULT, query="example person", engine="Brave"
    )
    reasons = _website_reasons(extract([finding]))
    assert any("search provider" in reason.lower() for reason in reasons), reasons


def test_a_manual_import_says_the_investigator_imported_it_and_names_the_engine():
    finding = _finding(
        "manual_search_recon",
        kind=FindingKind.MANUAL_SEARCH_RESULT,
        query='"Example Person" site:linkedin.com',
        engine="Google",
        url="https://www.linkedin.com/in/example-person",
        host="linkedin.com",
    )
    reasons = _website_reasons(extract([finding]))
    assert any("investigator imported" in reason.lower() for reason in reasons), reasons
    assert any("Google" in reason for reason in reasons), reasons
    assert not any("a configured search provider" in reason.lower() for reason in reasons), reasons
