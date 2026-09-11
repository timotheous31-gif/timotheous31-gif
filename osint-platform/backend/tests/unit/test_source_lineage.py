"""Which source combinations may amplify a score, and which may not.

The defect behind this file was measured, not theorised: ORCID and Wikidata both
publishing the same ORCID iD took two candidates from 0.1500 to 0.6175, because
the only question being asked was whether the two hostnames differed. Wikidata's
P496 values are routinely added by bots reading ORCID, so that was one fact,
copied once, scored as two parties agreeing.

The rule these tests hold the engine to:

    UNKNOWN INDEPENDENCE IS NOT INDEPENDENCE.
"""

from __future__ import annotations

import pytest

from app.correlation.confidence import default_engine
from app.correlation.lineage import (
    LINEAGE,
    STATUS_EFFECT,
    Independence,
    independence,
    lineage_for,
)

#: Every combination this platform can actually produce, and its ruling. The
#: table is the specification: a change in behaviour has to change a line here.
MATRIX: tuple[tuple[str, str, str, Independence], ...] = (
    # --- dependent: one ingests the other ---------------------------------
    ("orcid", "wikidata", "orcid", Independence.DEPENDENT),
    ("wikidata", "orcid", "orcid", Independence.DEPENDENT),
    ("orcid", "openalex", "orcid", Independence.DEPENDENT),
    ("crossref", "openalex", "doi", Independence.DEPENDENT),
    ("crossref", "orcid", "doi", Independence.DEPENDENT),
    ("crossref", "wikidata", "doi", Independence.DEPENDENT),
    ("github", "github_people", "github_login", Independence.DEPENDENT),
    ("github_people", "wikidata", "github_login", Independence.DEPENDENT),
    ("wikidata", "openalex", "wikidata", Independence.DEPENDENT),
    # --- dependent: both take the value from a common upstream ------------
    ("openalex", "wikidata", "doi", Independence.DEPENDENT),
    # --- dependent: the same source twice ---------------------------------
    ("orcid", "orcid", "orcid", Independence.DEPENDENT),
    # --- independent: two parties, each on its own authority --------------
    ("orcid", "github_people", "github_login", Independence.INDEPENDENT),
    ("orcid", "github", "github_login", Independence.INDEPENDENT),
    # --- unknown: nothing established either way --------------------------
    ("crossref", "github_people", "orcid", Independence.UNKNOWN),
    ("orcid", "crossref", "orcid", Independence.UNKNOWN),
    ("reddit", "orcid", "orcid", Independence.UNKNOWN),
    ("provider_search", "orcid", "orcid", Independence.UNKNOWN),
    ("manual_search_recon", "wikidata", "wikidata", Independence.UNKNOWN),
)


@pytest.mark.parametrize(("first", "second", "identifier", "expected"), MATRIX)
def test_the_independence_matrix(first, second, identifier, expected):
    verdict = independence(first, second, identifier)
    assert verdict.status is expected, verdict.reason
    assert verdict.reason, "every ruling must explain itself"


@pytest.mark.parametrize(("first", "second", "identifier", "expected"), MATRIX)
def test_only_established_independence_amplifies(first, second, identifier, expected):
    verdict = independence(first, second, identifier)
    assert verdict.amplifies is (expected is Independence.INDEPENDENT)
    assert STATUS_EFFECT[verdict.status]


def test_an_unrecorded_source_pair_is_unknown_not_independent():
    """The single most important property in this file.

    The previous implementation returned "independent" for every pair it had not
    been told about. A source nobody has audited must not be able to amplify a
    score by existing.
    """
    verdict = independence("some_new_collector", "another_new_collector", "orcid")
    assert verdict.status is Independence.UNKNOWN
    assert not verdict.amplifies
    assert "cannot be established as independent" in verdict.reason


def test_a_claims_own_reference_outranks_the_table():
    """Per-statement provenance can only make a ruling more conservative."""
    # ORCID and GitHub are independent for a GitHub login by the table...
    assert independence("orcid", "github_people", "github_login").amplifies
    # ...but not if the claim itself says it was copied from the other.
    verdict = independence(
        "orcid",
        "github_people",
        "github_login",
        first_claim={"imported_from": ["github_people"], "references": ["P854=github.com/x"]},
    )
    assert verdict.status is Independence.DEPENDENT
    assert "imported from the other" in verdict.reason


def test_a_relay_channel_is_never_a_second_party():
    """A search index can carry a page that copied another page."""
    for relay in ("provider_search", "manual_search_recon", "wayback"):
        verdict = independence(relay, "orcid", "orcid")
        assert verdict.status is Independence.UNKNOWN
        assert "relays third-party pages" in verdict.reason


def test_every_lineage_entry_explains_itself():
    """A lineage claim with no stated reason is an assertion, not a record."""
    for entry in LINEAGE:
        assert entry.note, f"{entry.source}/{entry.identifier} has no note"
        assert entry.source and entry.identifier
        assert not (entry.authoritative and entry.source in entry.ingests_from)
    # And the table is reachable by key.
    assert lineage_for("orcid", "orcid") is not None
    assert lineage_for("orcid", "nonexistent_identifier") is None


def test_the_amplified_rule_is_the_only_corroboration_rule():
    """So "no amplification" is a statement about one named rule, not a mood."""
    rule = default_engine.rules["independent_corroboration"]
    assert rule.score > 0
    corroboration_rules = [
        key for key in default_engine.rules if "corrobor" in key or "independent" in key
    ]
    assert corroboration_rules == ["independent_corroboration"]
