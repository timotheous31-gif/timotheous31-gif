"""The investigator-facing report: a view over the canonical model.

The point of these tests is not that the document renders — it is that the
document cannot say more than the model knows. A presentation layer is exactly
where an investigation report acquires a confident narrative nobody can trace,
so the invariants asserted here are:

* it is a **view**, not a second model — every format still reads one report;
* every statement carries a **basis**, and the bases are not interchangeable;
* an automated score and a human decision are **never merged**;
* weak candidates are **summarised in the body and retained in the appendix**,
  never dropped;
* a picture of a person is never presented as an identification;
* the existing Markdown, HTML and JSON exports are **byte-for-byte unchanged**.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from app.models import Case, Target
from app.models.entity import Entity
from app.models.enums import (
    AnalystDecision,
    DecisionSubject,
    EntityType,
    ImageFetchState,
    ReportFormat,
    TargetType,
)
from app.models.social import AnalystDecisionRecord, ImageEvidence
from app.reporting import (
    build_report,
    render_dossier,
    render_html,
    render_json,
    render_markdown,
)
from app.reporting.dossier import Basis, build_dossier, profile_image_for

SUBJECT = "Sara Samara"


@pytest.fixture
def person_case(db_session):
    """A PERSON case: strong candidate, weak ones, a decision, an image."""
    case = Case(name="Sara Samara — due diligence", description="Public-source review.")
    db_session.add(case)
    db_session.flush()
    db_session.add(
        Target(
            case_id=case.id,
            type=TargetType.PERSON,
            raw_input=SUBJECT,
            normalized_value="sara samara",
            attributes={"context": {"affiliations": ["Example Research Institute"]}},
        )
    )

    def candidate(name, url, score, anchors, affiliations=(), locations=()):
        entity = Entity(
            case_id=case.id,
            type=EntityType.PERSONA,
            display_name=name,
            canonical_value=f"person-candidate:{url}",
            confidence=score,
            confidence_reasons=["Name and anchor rules combined."],
            attributes={
                "role": "candidate",
                "candidate_name": name,
                "source": "openalex",
                "source_label": "OpenAlex",
                "reference_url": url,
                "corroborated_by": list(anchors),
                "affiliations": list(affiliations),
                "locations": list(locations),
            },
        )
        db_session.add(entity)
        db_session.flush()
        return entity

    strong = candidate(
        SUBJECT,
        "https://orcid.org/0000-0002-1825-0097",
        0.78,
        ["orcid", "affiliation"],
        ["Example Research Institute"],
        ["Amman, Jordan"],
    )
    weak = candidate("Sara Samari", "https://openalex.org/A2", 0.05, [])
    rejected = candidate(SUBJECT, "https://openalex.org/A9", 0.55, ["location"])

    db_session.add(
        AnalystDecisionRecord(
            case_id=case.id,
            subject_type=DecisionSubject.CANDIDATE,
            subject_id=rejected.id,
            decision=AnalystDecision.REJECTED,
            decided_by="a.mitchell",
            decided_at=datetime.now(UTC),
            note="Different person — publication dates rule it out.",
        )
    )
    db_session.add(
        ImageEvidence(
            case_id=case.id,
            candidate_entity_id=strong.id,
            image_url="https://avatars.example.org/u/ssamara.jpg",
            source_page_url="https://orcid.org/0000-0002-1825-0097",
            platform="orcid",
            fetch_state=ImageFetchState.REFERENCE_ONLY,
            origin="profile_avatar",
            evidence_class="PUBLIC_PROFILE",
            retrieved_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    return case, strong, weak, rejected


@pytest.fixture
def dossier(db_session, person_case):
    case, *_ = person_case
    return build_dossier(build_report(db_session, case.id))


# --- it is a view, not a second model ---------------------------------------


class TestOneSourceOfTruth:
    def test_the_other_three_formats_are_untouched(self, db_session, person_case):
        """The regression that matters most: this PR adds, it does not change.

        Rendered twice from one model — once before reading the dossier, once
        after — because a presentation layer that mutated the model it read
        would corrupt every other export, and would do it invisibly.
        """
        case, *_ = person_case
        model = build_report(db_session, case.id)

        before = (render_markdown(model), render_html(model), render_json(model))
        render_dossier(model)
        after = (render_markdown(model), render_html(model), render_json(model))

        assert before == after

    def test_it_reads_the_same_model_every_other_format_reads(self, db_session, person_case):
        """A claim in the dossier and the same claim in JSON cannot disagree."""
        case, _strong, _weak, _rejected = person_case
        model = build_report(db_session, case.id)
        exported = json.loads(render_json(model))
        html = render_dossier(model)

        scores = {
            item["attributes"].get("reference_url"): item["confidence"]
            for item in exported["entities"]
            if item["attributes"].get("role") == "candidate"
        }
        assert scores["https://orcid.org/0000-0002-1825-0097"] == pytest.approx(0.78)
        # The same number, printed to two places, is in the document.
        assert "0.78" in html

    def test_building_it_is_pure(self, db_session, person_case):
        case, *_ = person_case
        model = build_report(db_session, case.id)

        first = build_dossier(model).to_dict()
        second = build_dossier(model).to_dict()

        assert first == second

    def test_the_format_is_registered_end_to_end(self):
        from app.reporting.renderers import RENDERERS

        assert str(ReportFormat.DOSSIER) == "dossier"
        assert "dossier" in RENDERERS
        for existing in ("html", "md", "json"):
            assert existing in RENDERERS


# --- evidence discipline ----------------------------------------------------


class TestEveryStatementCarriesItsBasis:
    def test_every_statement_has_one_of_the_known_bases(self, dossier):
        known = {str(item) for item in Basis}
        for section in [*dossier.sections, *dossier.appendix]:
            for statement in section.statements:
                assert str(statement.basis) in known, statement.text
            for row in section.rows:
                if "basis" in row:
                    assert str(row["basis"]) in known, row

    def test_a_correlation_is_never_labelled_a_sourced_fact(self, dossier):
        """The mislabelling this vocabulary exists to prevent."""
        identity = next(s for s in dossier.sections if s.key == "identity_assessment")
        for row in identity.rows:
            if row.get("corroborated_by"):
                assert row["basis"] in {
                    str(Basis.CORRELATION),
                    str(Basis.ANALYST_DECISION),
                }

    def test_an_uncorroborated_candidate_is_an_unresolved_lead(self, db_session, person_case):
        case, *_ = person_case
        built = build_dossier(build_report(db_session, case.id))
        appendix = next(s for s in built.appendix if s.key == "low_confidence_candidates")

        weak = [row for row in appendix.rows if row["name"] == "Sara Samari"]
        assert weak, "the weak candidate should be in the appendix"
        assert weak[0]["basis"] == str(Basis.UNRESOLVED_LEAD)

    def test_the_executive_summary_is_a_case_record_not_a_sourced_fact(self, dossier):
        """It says what the run did, which no source published.

        Labelling "1 collector run failed" as a sourced fact would read as a
        source having published that, and would quietly devalue the label
        everywhere else it appears.
        """
        summary = next(s for s in dossier.sections if s.key == "executive_summary")
        for statement in summary.statements:
            assert statement.basis == Basis.CASE_RECORD, statement.text

    def test_the_document_explains_each_basis_to_its_reader(self, db_session, person_case):
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))

        for label in ("Sourced fact", "Correlation", "Analyst decision", "Unresolved lead"):
            assert label in html, label
        assert "not a probability" in html


class TestScoresAndDecisionsStaySeparate:
    def test_an_analyst_decision_never_edits_a_score(self, db_session, person_case):
        case, _strong, _weak, rejected = person_case
        model = build_report(db_session, case.id)

        entity = next(item for item in model.entities if item.id == str(rejected.id))
        assert entity.confidence == pytest.approx(0.55), "the decision left the score alone"

        built = build_dossier(model)
        rows = [
            row
            for section in [*built.sections, *built.appendix]
            for row in section.rows
            if row.get("name") == SUBJECT and row.get("score") == "0.55"
        ]
        assert rows, "the rejected candidate is still reported, with its score"

    def test_a_rejected_candidate_leaves_the_body_but_stays_in_the_appendix(self, dossier):
        body = next(s for s in dossier.sections if s.key == "identity_assessment")
        appendix = next(s for s in dossier.appendix if s.key == "low_confidence_candidates")

        assert not [row for row in body.rows if row["score"] == "0.55"]
        assert [row for row in appendix.rows if row["score"] == "0.55"]

    def test_decisions_have_their_own_section_naming_who_decided(self, dossier):
        section = next(s for s in dossier.sections if s.key == "analyst_decisions")

        assert section.statements
        for statement in section.statements:
            assert statement.basis == Basis.ANALYST_DECISION
            assert statement.source == "a.mitchell"
        # Named, not an opaque id — a reader cannot audit "REJECTED 3f8e7549".
        assert any(SUBJECT in statement.text for statement in section.statements)

    def test_the_score_is_never_presented_as_a_percentage(self, db_session, person_case):
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))

        assert "Correlation score" in html

        # Checked over the reported content only. Two exemptions, both
        # legitimate: the stylesheet contains `width: 100%`, and the "how to
        # read" legend quotes a percentage precisely in order to reject it
        # ("two records are never '70% the same person'").
        prose = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
        prose = re.sub(r'<div class="legend">.*?</div>', "", prose, flags=re.DOTALL)
        assert not re.search(r"\b\d{1,3}\s?%", prose), "a score was rendered as a percentage"

        # And the legend really does carry that warning.
        assert "never a probability" in html or "not a probability" in html


class TestWeakCandidatesAreSummarisedNotDropped:
    def test_the_appendix_holds_them_and_the_body_does_not(self, dossier):
        body = next(s for s in dossier.sections if s.key == "identity_assessment")
        appendix = next(s for s in dossier.appendix if s.key == "low_confidence_candidates")

        assert "Sara Samari" not in {row["name"] for row in body.rows}
        assert "Sara Samari" in {row["name"] for row in appendix.rows}

    def test_no_candidate_is_in_both_and_none_is_lost(self, db_session, person_case):
        case, *_ = person_case
        model = build_report(db_session, case.id)
        built = build_dossier(model)

        body = next(s for s in built.sections if s.key == "identity_assessment")
        appendix = next(s for s in built.appendix if s.key == "low_confidence_candidates")
        shown = [row["name"] for row in body.rows] + [row["name"] for row in appendix.rows]

        candidates = [item for item in model.entities if item.attributes.get("role") == "candidate"]
        assert len(shown) == len(candidates), "every candidate is reported exactly once"

    def test_the_appendix_says_nothing_was_deleted(self, db_session, person_case):
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))
        assert "Retained" in html or "retained" in html


# --- the picture ------------------------------------------------------------


class TestAProfileImageIsNeverAnIdentification:
    def test_it_is_captioned_with_its_source_and_retrieval_date(self, db_session, person_case):
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))

        assert "https://orcid.org/0000-0002-1825-0097" in html
        assert "Retrieved:" in html

    def test_the_caption_refuses_the_inference_a_photograph_invites(self, db_session, person_case):
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))

        assert "no facial recognition" in html
        assert "no biometric matching" in html

    def test_an_unattributed_image_is_never_shown_as_the_subject(self, db_session, person_case):
        """A picture filed against no candidate is not the subject's picture.

        Showing it under "subject profile" would be the report making an
        attribution the case does not hold.
        """
        case, *_ = person_case
        db_session.add(
            ImageEvidence(
                case_id=case.id,
                candidate_entity_id=None,
                image_url="https://images.example.org/unattributed.jpg",
                source_page_url="https://example.org/page",
                platform="web",
                fetch_state=ImageFetchState.REFERENCE_ONLY,
                origin="page_image",
                evidence_class="PUBLIC_PROFILE",
            )
        )
        db_session.commit()

        chosen = profile_image_for(build_report(db_session, case.id))
        assert chosen is None or "unattributed" not in chosen.image_url

    def test_no_image_is_shown_for_a_case_with_no_person_target(self, db_session):
        case = Case(name="Domain only")
        db_session.add(case)
        db_session.flush()
        db_session.add(
            Target(
                case_id=case.id,
                type=TargetType.DOMAIN,
                raw_input="example.com",
                normalized_value="example.com",
            )
        )
        db_session.commit()

        assert profile_image_for(build_report(db_session, case.id)) is None


# --- structure and safety ---------------------------------------------------


class TestTheDocument:
    REQUIRED = [
        "executive_summary",
        "subject_profile",
        "key_findings",
        "identity_assessment",
        "profiles",
        "contacts",
        "affiliations",
        "geography",
        "relationships",
        "timeline",
        "analyst_decisions",
        "coverage",
        "evidence",
        "limitations",
    ]

    def test_every_required_section_is_present_and_in_order(self, dossier):
        assert [section.key for section in dossier.sections] == self.REQUIRED

    def test_an_empty_section_says_so_rather_than_vanishing(self, dossier):
        """Silence reads as "not applicable"; the truth is usually "found nothing"."""
        empty = [section for section in dossier.sections if section.is_empty]
        assert empty, "this fixture should leave at least one section empty"
        for section in empty:
            assert section.empty_note

    def test_collected_text_cannot_execute_as_markup(self, db_session, person_case):
        """Report content includes text from third-party sites."""
        case, *_ = person_case
        db_session.add(
            Entity(
                case_id=case.id,
                type=EntityType.PERSONA,
                display_name="<script>alert(1)</script>",
                canonical_value="person-candidate:https://evil.example/x",
                confidence=0.5,
                confidence_reasons=[],
                attributes={
                    "role": "candidate",
                    "candidate_name": "<script>alert(1)</script>",
                    "reference_url": "https://evil.example/x",
                    "corroborated_by": [],
                },
            )
        )
        db_session.commit()

        html = render_dossier(build_report(db_session, case.id))

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_it_is_self_contained(self, db_session, person_case):
        """No external stylesheet or font: opening it discloses nothing."""
        case, *_ = person_case
        html = render_dossier(build_report(db_session, case.id))

        assert "<link" not in html
        assert "@page" in html, "the print layout is part of the deliverable"

    def test_it_renders_for_a_case_with_nothing_in_it(self, db_session):
        """The hardest case for a report that must not invent narrative."""
        case = Case(name="Empty case")
        db_session.add(case)
        db_session.commit()

        html = render_dossier(build_report(db_session, case.id))

        assert "Empty case" in html
        assert "Executive summary" in html
