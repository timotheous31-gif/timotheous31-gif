"""What the report says, for the three things a reader can be misled by.

Each test here corresponds to a claim the platform makes in words rather than in
numbers, and each one was wrong in a specific way before this pass:

* a citizenship a source states was collected and then shown nowhere but raw JSON;
* a profile found twice reported only the route that found it last, which was
  usually the weaker one;
* a correlation score was labelled "probable match" and rendered as a percentage,
  which states a likelihood nobody computed.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from app.models import Case, Target
from app.models.enums import TargetType
from app.reporting.model import build_report
from app.reporting.renderers import render_markdown

NAME = "Example Public Figure"
LINKEDIN = "https://www.linkedin.com/in/example-person"
GITHUB_PAGE = "https://github.com/example-person"


def _case_with_target(session, name=NAME):
    case = Case(name="Hardening")
    session.add(case)
    session.flush()
    target = Target(
        case_id=case.id,
        type=TargetType.PERSON,
        raw_input=name,
        normalized_value=name.lower(),
        attributes={"display_name": name, "context": {}},
    )
    session.add(target)
    session.flush()
    return case, target


# --------------------------------------------------- source-claimed citizenship


def test_a_source_claimed_citizenship_is_shown_attributed_and_fenced_off(db_session):
    from app.models import Finding
    from app.models.enums import Classification, FindingKind

    case, target = _case_with_target(db_session)
    db_session.add(
        Finding(
            case_id=case.id,
            target_id=target.id,
            kind=FindingKind.PERSON_CANDIDATE,
            title=NAME,
            data={
                "url": "https://www.wikidata.org/wiki/Q999999",
                "candidate_name": NAME,
                "source": "wikidata",
                "source_label": "Wikidata",
                "citizenship_claims": ["Pakistan"],
                "citizenship_interpretation": (
                    "Wikidata publishes this as a country-of-citizenship claim."
                ),
                "locations": [],
            },
            collector="wikidata",
            source_url="https://www.wikidata.org/wiki/Q999999",
            confidence=0.15,
            confidence_reasons=["Only the displayed personal name matches"],
            classification=Classification.PERSONAL,
            dedupe_key=f"person-candidate:{_uuid.uuid4()}",
        )
    )
    db_session.commit()

    report = build_report(db_session, case.id)
    assert len(report.citizenship_claims) == 1
    claim = report.citizenship_claims[0]
    assert claim.country == "Pakistan"
    assert claim.source_label == "Wikidata"
    described = claim.to_dict()
    # Every "this is not" travels with the claim, in the data, not just the prose.
    assert described["infers_nationality"] is False
    assert described["is_location"] is False
    assert described["is_residence"] is False
    assert described["corroborates_location_anchor"] is False

    markdown = render_markdown(report)
    assert "## Source-claimed citizenship" in markdown
    assert "| Pakistan | Wikidata |" in markdown
    for promise in (
        "not a residence",
        "not a current location",
        "corroborates no country or city",
    ):
        assert promise in markdown, promise
    # And it is still not a location anywhere in the model.
    assert report.findings[0].data["locations"] == []


def test_a_citizenship_claim_still_corroborates_no_supplied_country(db_session):
    """The scoring half, asserted beside the display half so they cannot drift."""
    from app.collectors.person import PersonCandidate, PersonContext, anchor_matches

    candidate = PersonCandidate(
        url="https://www.wikidata.org/wiki/Q1",
        name=NAME,
        extra={"citizenship_claims": ["Pakistan"]},
    )
    assert anchor_matches(candidate, PersonContext(country="Pakistan")) == []


# ------------------------------------------------------------ discovery routes


def test_a_profile_found_twice_keeps_both_routes_and_reports_the_stronger(db_session):
    from app.services.social_profiles import (
        DISCOVERY_PROVIDER_SEARCH,
        DISCOVERY_PUBLISHED_LINK,
        profiles_for_case,
        record_profile,
    )

    case, target = _case_with_target(db_session)

    first = record_profile(
        db_session,
        case_id=case.id,
        url=LINKEDIN,
        target=target,
        collector="github_people",
        evidence_class="api_fetched",
        linked_from=GITHUB_PAGE,
        discovery_method=DISCOVERY_PUBLISHED_LINK,
    )
    db_session.flush()
    assert first is not None
    before = first.confidence

    second = record_profile(
        db_session,
        case_id=case.id,
        url=LINKEDIN,
        target=target,
        collector="provider_search",
        evidence_class="provider_search_result",
        discovery_method=DISCOVERY_PROVIDER_SEARCH,
    )
    db_session.commit()

    assert second is not None and second.id == first.id, "one page is one profile row"
    assert len(profiles_for_case(db_session, case.id)) == 1
    # Both routes kept, strongest first, and the single-valued field reports it.
    assert second.discovery_methods == [DISCOVERY_PUBLISHED_LINK, DISCOVERY_PROVIDER_SEARCH]
    assert second.discovery_method == DISCOVERY_PUBLISHED_LINK
    # A second route is provenance, not a second vote.
    assert second.confidence == pytest.approx(before)
    assert second.corroborated_by == []

    report = build_report(db_session, case.id)
    item = next(entry for entry in report.social_profiles if LINKEDIN in entry.profile_url)
    assert item.discovery_methods == [DISCOVERY_PUBLISHED_LINK, DISCOVERY_PROVIDER_SEARCH]
    markdown = render_markdown(report)
    assert "How it was found: linked from another public page the subject controls" in markdown
    assert "additional route, not extra corroboration" in markdown


def test_every_published_link_origin_is_kept(db_session):
    from app.services.social_profiles import DISCOVERY_PUBLISHED_LINK, record_profile

    case, target = _case_with_target(db_session)
    other = "https://example.org/team/example-person"
    for origin in (GITHUB_PAGE, other):
        profile = record_profile(
            db_session,
            case_id=case.id,
            url=LINKEDIN,
            target=target,
            collector="github_people",
            evidence_class="api_fetched",
            linked_from=origin,
            discovery_method=DISCOVERY_PUBLISHED_LINK,
        )
        db_session.flush()
    db_session.commit()
    assert profile is not None
    assert profile.discovered_from_all == [GITHUB_PAGE, other]
    # One published-link sentence per origin page. The route sentence names the
    # *kind* of route once, which is a different statement.
    assert (
        sum(1 for reason in profile.match_reasons if "This profile is linked from" in reason) == 2
    )


# ------------------------------------------------------- correlation, not odds


def test_no_rendered_report_describes_a_score_as_a_probability(db_session):
    from app.models import Finding
    from app.models.enums import Classification, FindingKind

    case, target = _case_with_target(db_session)
    db_session.add(
        Finding(
            case_id=case.id,
            target_id=target.id,
            kind=FindingKind.PERSON_CANDIDATE,
            title=NAME,
            data={"url": LINKEDIN, "candidate_name": NAME, "source": "provider_search"},
            collector="provider_search",
            source_url=LINKEDIN,
            confidence=0.72,
            confidence_reasons=["The same distinctive username appears on both platforms"],
            classification=Classification.PERSONAL,
            dedupe_key=f"person-candidate:{_uuid.uuid4()}",
        )
    )
    db_session.commit()

    markdown = render_markdown(build_report(db_session, case.id))
    lowered = markdown.lower()
    assert "correlation score" in lowered
    assert "these are correlation scores, not probabilities" in lowered
    # The band vocabulary no longer states a likelihood.
    assert "moderate correlation" in lowered
    assert "probable match" not in lowered
    assert "possible match" not in lowered
    assert "likely match" not in lowered
    # And no score is rendered as a percentage.
    assert "72%" not in markdown


def test_the_display_bands_cover_every_stored_band():
    """So a new band can never fall through to the raw enum name in a report."""
    from app.correlation.confidence import classify
    from app.reporting.renderers import CORRELATION_BANDS, correlation_band

    for score in (0.0, 0.49, 0.5, 0.69, 0.7, 0.89, 0.9, 1.0):
        band = str(classify(score))
        assert band in CORRELATION_BANDS, band
        assert "match" not in correlation_band(band).lower()
