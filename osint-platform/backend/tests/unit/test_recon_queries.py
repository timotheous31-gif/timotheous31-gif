"""Recon query generation.

The generator is the one place where the platform decides *what it is willing to
look for* about a named person, so the tests here are as much about what it
refuses to emit as about what it produces.
"""

from __future__ import annotations

import pytest

from app.collectors.person import PersonContext
from app.services.recon import (
    FAMILY_ACADEMIC,
    FAMILY_ANCHOR,
    FAMILY_DOCUMENT,
    FAMILY_SOCIAL,
    FORBIDDEN_TERMS,
    MAX_QUERIES,
    generate_queries,
    is_permitted,
)

NAME = "Timotheous Samar"


def queries_for(context: PersonContext | None = None) -> list[str]:
    return [item.query for item in generate_queries(NAME, context)]


# ------------------------------------------------------------ what it produces


def test_the_plain_quoted_name_is_always_generated():
    assert f'"{NAME}"' in queries_for()


@pytest.mark.parametrize(
    "platform",
    ["LinkedIn", "Instagram", "Facebook", "YouTube", "Snapchat"],
)
def test_each_major_platform_gets_a_query(platform):
    assert f'"{NAME}" {platform}' in queries_for()


@pytest.mark.parametrize(
    "site",
    ["linkedin.com", "instagram.com", "facebook.com", "youtube.com", "github.com", "orcid.org"],
)
def test_each_major_platform_gets_a_site_query(site):
    assert f'"{NAME}" site:{site}' in queries_for()


def test_research_and_document_families_are_generated():
    generated = queries_for()
    assert f'"{NAME}" research' in generated
    assert f'"{NAME}" publication' in generated
    assert f'"{NAME}" filetype:pdf' in generated
    assert f'"{NAME}" site:edu' in generated


def test_every_query_explains_itself():
    for query in generate_queries(NAME, PersonContext(organizations=("Example Ltd",))):
        assert query.rationale, f"{query.query} has no rationale"
        assert query.family
        assert query.priority > 0


def test_generation_is_deterministic():
    context = PersonContext(organizations=("Example Ltd",), known_usernames=("octocat",))
    assert [q.query for q in generate_queries(NAME, context)] == [
        q.query for q in generate_queries(NAME, context)
    ]


def test_an_empty_name_generates_nothing():
    assert generate_queries("   ") == []


def test_the_list_stays_human_sized():
    context = PersonContext(
        organizations=tuple(f"Org {index}" for index in range(20)),
        schools=tuple(f"School {index}" for index in range(20)),
        known_usernames=tuple(f"handle{index}" for index in range(20)),
    )
    assert len(generate_queries(NAME, context)) <= MAX_QUERIES


# ------------------------------------------------------------------- anchors


def test_a_supplied_organization_produces_an_anchored_query():
    context = PersonContext(organizations=("Example University",))
    generated = generate_queries(NAME, context)
    anchored = [item for item in generated if item.family == FAMILY_ANCHOR]
    assert f'"{NAME}" "Example University"' in [item.query for item in anchored]
    assert any("organization" in item.anchors_used for item in anchored)


def test_a_supplied_username_produces_both_paired_and_standalone_queries():
    generated = queries_for(PersonContext(known_usernames=("octocat",)))
    assert f'"{NAME}" "octocat"' in generated
    assert '"octocat"' in generated


def test_a_supplied_orcid_is_the_highest_priority_query():
    generated = generate_queries(NAME, PersonContext(orcid="0000-0002-1825-0097"))
    assert generated[0].query == '"0000-0002-1825-0097"'
    assert generated[0].anchors_used == ["orcid"]


def test_a_supplied_website_becomes_a_site_query():
    generated = queries_for(PersonContext(websites=("https://example.com/about",)))
    assert f'"{NAME}" site:example.com' in generated


def test_anchored_queries_outrank_unanchored_ones():
    generated = generate_queries(NAME, PersonContext(organizations=("Example Ltd",)))
    anchored = next(item for item in generated if item.family == FAMILY_ANCHOR)
    social = next(item for item in generated if item.family == FAMILY_SOCIAL)
    assert generated.index(anchored) < generated.index(social)


def test_a_supplied_place_is_only_ever_paired_with_the_name():
    generated = queries_for(PersonContext(city="Delft"))
    assert f'"{NAME}" "Delft"' in generated
    # Never a bare locator: that searches for a place, not for a reference.
    assert '"Delft"' not in generated


# -------------------------------------------------------------- deduplication


def test_duplicate_queries_are_collapsed():
    # The same value supplied as both an organisation and a school would
    # otherwise generate the identical query twice.
    context = PersonContext(organizations=("Example University",), schools=("Example University",))
    generated = queries_for(context)
    assert generated.count(f'"{NAME}" "Example University"') == 1


def test_deduplication_ignores_case_and_spacing():
    context = PersonContext(known_usernames=("octocat", "OCTOCAT", " octocat "))
    generated = queries_for(context)
    assert generated.count('"octocat"') == 1


# ------------------------------------------------------- what it refuses to do


@pytest.mark.parametrize(
    "term",
    [
        "home address",
        "phone number",
        "password",
        "date of birth",
        "family",
        "salary",
        "criminal record",
        "coordinates",
    ],
)
def test_invasive_terms_are_never_generated(term):
    """The list is fixed in code, so this checks the whole output at once."""
    context = PersonContext(
        organizations=("Example Ltd",),
        schools=("Example University",),
        known_usernames=("octocat",),
        occupation="researcher",
        city="Delft",
        country="Netherlands",
        websites=("https://example.com",),
        orcid="0000-0002-1825-0097",
    )
    joined = " ".join(queries_for(context)).lower()
    assert term not in joined


def test_no_generated_query_would_be_rejected_by_the_screen():
    context = PersonContext(organizations=("Example Ltd",), known_usernames=("octocat",))
    for query in generate_queries(NAME, context):
        assert is_permitted(query.query), query.query


@pytest.mark.parametrize("term", sorted(FORBIDDEN_TERMS)[:8])
def test_the_screen_rejects_each_forbidden_term(term):
    assert is_permitted(f'"{NAME}" {term}') is False


def test_the_screen_matches_whole_words_only():
    """An anchor that merely contains a forbidden substring is still allowed."""
    assert is_permitted('"Timotheous Samar" "Addressograph Ltd"') is True
    assert is_permitted('"Timotheous Samar" address') is False


def test_an_anchor_carrying_a_forbidden_term_is_dropped_not_emitted():
    context = PersonContext(organizations=("Home Address Registry",))
    for query in queries_for(context):
        assert "address" not in query.lower()


def test_no_query_family_targets_a_person_rather_than_a_reference():
    """Every family pairs the name with a *place to look*, never with a fact to
    extract about a private individual."""
    generated = generate_queries(NAME, PersonContext(occupation="researcher"))
    families = {item.family for item in generated}
    assert families <= {
        "general",
        FAMILY_SOCIAL,
        FAMILY_ACADEMIC,
        FAMILY_DOCUMENT,
        FAMILY_ANCHOR,
    }
