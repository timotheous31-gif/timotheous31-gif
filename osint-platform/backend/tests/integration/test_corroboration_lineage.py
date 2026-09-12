"""Copied claims must not become independent corroboration.

Every case here is an agreement between two sources on one identifier, and the
question is always the same: can the platform establish that the two did not take
the value from each other? When it can, the named ``independent_corroboration``
rule fires and a score rises. When it cannot — including when it simply does not
know — the agreement is kept, shown, and scored at nothing.

That asymmetry is deliberate and it is the fix: the previous implementation
treated "two different hostnames" as "two independent parties", so ORCID and
Wikidata agreeing on an iD that Wikidata bots import *from ORCID* took two
candidates from 0.1500 to 0.6175.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from app.correlation.corroboration import CorroborationService
from app.models import Case, Entity
from app.models.enums import EntityType

NAME = "Example Public Figure"
ORCID = "0000-0002-1825-0097"
DOI = "10.1234/example.2026"
LOGIN = "example-person"


def _candidate(session, case_id, source, identifiers, *, claim_lineage=None):
    entity = Entity(
        case_id=case_id,
        type=EntityType.PERSONA,
        canonical_value=f"person-candidate:https://{source}.example.org/{_uuid.uuid4()}",
        display_name=NAME,
        confidence=0.15,
        confidence_reasons=["Only the displayed personal name matches"],
        attributes={
            "source": source,
            "identifiers": identifiers,
            "signal_keys": ["same_person_name"],
            **({"claim_lineage": claim_lineage} if claim_lineage else {}),
        },
    )
    session.add(entity)
    session.flush()
    return entity


def _case(session, name):
    case = Case(name=name)
    session.add(case)
    session.flush()
    return case


def _run(session, case, pairs):
    entities = [
        _candidate(session, case.id, source, ids, claim_lineage=lineage)
        for source, ids, lineage in pairs
    ]
    summary = CorroborationService().run(session, case.id)
    session.flush()
    return summary, entities


def _shared(entity):
    return (entity.attributes or {}).get("shared_identifiers") or []


# ------------------------------------------------- agreements that score nothing


@pytest.mark.parametrize(
    ("label", "pairs", "expect_reason"),
    [
        (
            "ORCID -> Wikidata, the iD Wikidata bots import",
            [("orcid", {"orcid": ORCID}, None), ("wikidata", {"orcid": ORCID}, None)],
            "takes ORCID values from orcid",
        ),
        (
            "ORCID -> OpenAlex, which indexes ORCID",
            [("orcid", {"orcid": ORCID}, None), ("openalex", {"orcid": ORCID}, None)],
            "takes ORCID values from orcid",
        ),
        (
            "Crossref -> OpenAlex, one deposit seen twice",
            [("crossref", {"doi": DOI}, None), ("openalex", {"doi": DOI}, None)],
            "takes DOI values from crossref",
        ),
        (
            "OpenAlex and Wikidata, both importing Crossref",
            [("openalex", {"doi": DOI}, None), ("wikidata", {"doi": DOI}, None)],
            "both take DOI values from crossref",
        ),
        (
            "unknown lineage: nothing is established either way",
            [("crossref", {"orcid": ORCID}, None), ("reddit", {"orcid": ORCID}, None)],
            "cannot be established as independent",
        ),
        (
            "a relay channel is never a second party",
            [("provider_search", {"orcid": ORCID}, None), ("openalex", {"orcid": ORCID}, None)],
            "relays third-party pages",
        ),
    ],
)
def test_an_agreement_without_established_independence_changes_no_score(
    db_session, label, pairs, expect_reason
):
    case = _case(db_session, label)
    summary, entities = _run(db_session, case, pairs)

    assert summary.corroborations == []
    assert summary.shared_identifiers, "the agreement is kept, not discarded"
    assert summary.duplicate_pairs_ignored == 1
    assert summary.entities_strengthened == 0

    for entity in entities:
        assert entity.confidence == pytest.approx(0.15), "no score moved"
        assert not (entity.attributes or {}).get("corroborating_sources")
        shared = _shared(entity)
        assert shared, "and it is recorded where an investigator will see it"
        assert expect_reason in shared[0]["reason"], shared[0]["reason"]
        assert shared[0]["independence"] in {"DEPENDENT", "UNKNOWN"}


def test_a_claims_own_provenance_overrides_an_otherwise_independent_pair(db_session):
    """Per-statement lineage can only make a ruling more conservative."""
    case = _case(db_session, "stated provenance")
    summary, entities = _run(
        db_session,
        case,
        [
            ("orcid", {"github_login": LOGIN}, {"github_login": {"imported_from": ["github"]}}),
            ("github", {"github_login": LOGIN}, None),
        ],
    )
    assert summary.corroborations == []
    assert summary.shared_identifiers
    assert "imported from the other" in summary.shared_identifiers[0].reason
    for entity in entities:
        assert entity.confidence == pytest.approx(0.15)


# ------------------------------------------------------ the one that does count


def test_two_sources_with_established_independence_do_corroborate(db_session):
    """ORCID and GitHub on a GitHub login: two parties, neither copying the other.

    The researcher asserts the link on their ORCID record; GitHub issues the login.
    That is a real second party, so the named rule fires — which also proves the
    conservative path above is a ruling and not a blanket refusal.
    """
    case = _case(db_session, "established independence")
    summary, entities = _run(
        db_session,
        case,
        [
            ("orcid", {"github_login": LOGIN}, None),
            ("github_people", {"github_login": LOGIN}, None),
        ],
    )
    assert len(summary.corroborations) == 1
    agreement = summary.corroborations[0]
    assert agreement.status.value == "INDEPENDENT"
    assert "independently publish" in agreement.reason
    assert summary.entities_strengthened == 2
    for entity in entities:
        assert entity.confidence > 0.15
        assert set((entity.attributes or {}).get("corroborating_sources") or []) == {
            "orcid",
            "github_people",
        } or set((entity.attributes or {}).get("corroborating_sources") or []) == {
            "orcid",
            "github",
        }
        # Still a candidate: corroboration strengthens a case for review, never
        # closes it.
        assert entity.confidence < 0.95
        assert not _shared(entity), "an established corroboration is not a bare shared identifier"


def test_one_source_twice_is_not_two_sources(db_session):
    case = _case(db_session, "same source twice")
    summary, entities = _run(
        db_session, case, [("orcid", {"orcid": ORCID}, None), ("orcid", {"orcid": ORCID}, None)]
    )
    assert summary.corroborations == []
    assert "one source repeating itself" in summary.shared_identifiers[0].reason
    for entity in entities:
        assert entity.confidence == pytest.approx(0.15)


def test_the_summary_says_which_claim_it_is_making(db_session):
    """A report must be able to print the difference, not just compute it."""
    case = _case(db_session, "wording")
    summary, _ = _run(
        db_session, case, [("orcid", {"orcid": ORCID}, None), ("wikidata", {"orcid": ORCID}, None)]
    )
    described = summary.as_dict()
    assert described["corroborations"] == []
    entry = described["shared_identifiers"][0]
    assert entry["label"] == "Same value in two indexes (one may be copied from the other)"
    assert entry["scoring_effect"] == "No effect on confidence. Shown as a lead to verify."
    assert "independently corroborate" not in entry["reason"]


def test_a_candidate_carrying_no_identifier_cannot_agree_with_anything(db_session):
    """Provider-search results carry no identifiers, so they cannot amplify at all."""
    case = _case(db_session, "no identifiers")
    summary, entities = _run(
        db_session, case, [("provider_search", {}, None), ("provider_search", {}, None)]
    )
    assert summary.corroborations == []
    assert summary.shared_identifiers == []
    for entity in entities:
        assert entity.confidence == pytest.approx(0.15)


def test_wikidata_publishes_its_orcid_claim_with_its_own_provenance(db_session):
    """The collector half: a P496 value arrives with the references behind it."""
    from app.collectors.wikidata import _claim_lineage

    entity = {
        "claims": {
            "P496": [
                {
                    "mainsnak": {"datavalue": {"value": ORCID}},
                    "references": [
                        {"snaks": {"P248": [{"datavalue": {"value": {"id": "Q51044"}}}]}},
                        {
                            "snaks": {
                                "P854": [{"datavalue": {"value": "https://orcid.org/" + ORCID}}]
                            }
                        },
                    ],
                }
            ]
        }
    }
    lineage = _claim_lineage(entity, "P496")
    assert lineage["imported_from"] == ["orcid"]
    assert any("P248=Q51044" in item for item in lineage["references"])


def test_an_unreferenced_wikidata_claim_is_unknown_not_independent(db_session):
    from app.collectors.wikidata import _claim_lineage

    entity = {"claims": {"P496": [{"mainsnak": {"datavalue": {"value": ORCID}}}]}}
    assert _claim_lineage(entity, "P496") == {}
    # And the table still rules the pair dependent, because Wikidata is not where
    # an ORCID iD is issued.
    from app.correlation.lineage import Independence, independence

    assert independence("wikidata", "orcid", "orcid").status is Independence.DEPENDENT


def test_the_corroboration_count_on_a_run_only_counts_real_corroboration(db_session):
    """So the job result and the report cannot overstate what was established."""
    case = _case(db_session, "counting")
    summary, _ = _run(
        db_session,
        case,
        [
            ("orcid", {"orcid": ORCID, "github_login": LOGIN}, None),
            ("wikidata", {"orcid": ORCID}, None),
            ("github_people", {"github_login": LOGIN}, None),
        ],
    )
    # The ORCID agreement is dependent; the GitHub-login agreement is independent.
    assert len(summary.corroborations) == 1
    assert summary.corroborations[0].identifier == "github_login"
    assert len(summary.shared_identifiers) == 1
    assert summary.shared_identifiers[0].identifier == "orcid"


def _unused(value):  # pragma: no cover - keeps the select import honest
    return list(select(Entity).where(Entity.id == value))


# ------------------------------------------------------------- in the report


def test_the_report_prints_the_two_kinds_of_agreement_differently(db_session):
    """A reader must not have to know which key a list was stored under."""
    from app.reporting.model import build_report
    from app.reporting.renderers import render_markdown

    case = _case(db_session, "report wording")
    _run(
        db_session,
        case,
        [
            ("orcid", {"orcid": ORCID, "github_login": LOGIN}, None),
            ("wikidata", {"orcid": ORCID}, None),
            ("github_people", {"github_login": LOGIN}, None),
        ],
    )
    db_session.commit()

    report = build_report(db_session, case.id)
    kinds = {item.independence for item in report.source_agreements}
    assert kinds == {"INDEPENDENT", "DEPENDENT"}
    for item in report.source_agreements:
        assert item.scoring_effect
        assert item.corroborates is (item.independence == "INDEPENDENT")

    markdown = render_markdown(report)
    assert "## Identifier agreement between sources" in markdown
    assert "**Independently corroborated**" in markdown
    assert "Same identifier in more than one index — not corroboration" in markdown
    assert "Unknown independence is not independence." in markdown
    # The dependent row must not be described with the corroboration wording.
    dependent = next(item for item in report.source_agreements if not item.corroborates)
    assert "independently publish" not in dependent.reason
