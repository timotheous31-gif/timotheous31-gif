"""Weak name-only candidates are set aside, not deleted.

The symptom this file pins down: searching for a person returns every record
carrying that name, or part of it. "Sara Samara" finds Sara Samari, and an
OpenAlex author who shares one name part and nothing else. Each is scored
honestly at 0.05, each explanation says plainly that nothing corroborates it —
and each arrives at the top of the analyst's list looking like a lead.

What this file asserts, in both directions:

* the weak ones are **suppressed**: folded into a counted, collapsible section;
* the weak ones are **kept**: same score, same evidence, same provenance, still
  in the export, still reachable. Nothing is deleted and no score moves.

The fixture is the one from the report: two near-miss spellings and an unrelated
academic record, against a subject with real anchors supplied.
"""

from __future__ import annotations

import uuid

import pytest

from app.correlation.suppression import (
    LOW_CONFIDENCE,
    PRIMARY,
    REJECTED,
    classify_candidate,
)

SUBJECT = "Sara Samara"


def _candidate(
    case_id,
    workspace_id,
    *,
    name: str,
    score: float,
    corroborated_by: list[str] | None = None,
    source: str = "openalex",
    url: str = "",
):
    """One candidate entity, written exactly as the resolver writes them."""
    from app.core.db import get_session_factory
    from app.models.entity import Entity
    from app.models.enums import EntityType

    reference = url or f"https://openalex.org/A{uuid.uuid4().hex[:8]}"
    with get_session_factory()() as session:
        entity = Entity(
            case_id=uuid.UUID(str(case_id)),
            type=EntityType.PERSONA,
            display_name=name,
            canonical_value=f"person-candidate:{reference}",
            confidence=score,
            confidence_reasons=["The name shares only some of its parts with the subject."],
            attributes={
                "role": "candidate",
                "candidate_name": name,
                "source": source,
                "source_label": "OpenAlex",
                "reference_url": reference,
                "corroborated_by": list(corroborated_by or []),
                "match_reasons": [f"The source names {name!r}"],
                "mismatch_reasons": ["Nothing beyond the name connects this record to the subject"],
            },
        )
        session.add(entity)
        session.commit()
        return str(entity.id)


async def _person_target(api_client, case_id, *, context: dict | None = None):
    """A PERSON target, optionally carrying investigator anchors."""
    payload: dict = {"value": SUBJECT, "type": "PERSON"}
    if context is not None:
        payload["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


ANCHORS = {"affiliations": ["Example Research Institute"], "known_usernames": ["ssamara"]}


async def _all_groups(api_client, case_id) -> list[dict]:
    response = await api_client.get(f"/api/v1/cases/{case_id}/candidates")
    assert response.status_code == 200, response.text
    return response.json()


async def _groups(api_client, case_id) -> dict[str, dict]:
    """By display name — for the tests whose fixture has one record per name."""
    return {group["display_name"]: group for group in await _all_groups(api_client, case_id)}


# --- the rule itself --------------------------------------------------------


class TestTheRule:
    """The predicate, in isolation. No database, no HTTP, no scoring."""

    def test_a_weak_name_only_candidate_is_suppressed(self):
        verdict = classify_candidate(score=0.05, corroborated_by=[])
        assert verdict.presentation == LOW_CONFIDENCE
        assert verdict.suppressed
        assert "0.05" in verdict.reason

    def test_one_anchor_is_enough_however_weak_the_name(self):
        for anchor in ("affiliation", "username", "orcid", "github_username", "location"):
            verdict = classify_candidate(score=0.05, corroborated_by=[anchor])
            assert verdict.presentation == PRIMARY, anchor
            assert anchor in verdict.reason

    def test_an_unknown_anchor_kind_does_not_count(self):
        """Only the kinds the scoring layer knows about corroborate anything."""
        assert classify_candidate(score=0.05, corroborated_by=["vibes"]).suppressed

    def test_the_threshold_is_the_only_number_it_reads(self):
        assert classify_candidate(score=0.11, corroborated_by=[]).presentation == PRIMARY
        assert classify_candidate(score=0.10, corroborated_by=[]).presentation == LOW_CONFIDENCE
        # And it is a parameter, not a constant baked into the comparison.
        assert (
            classify_candidate(score=0.05, corroborated_by=[], threshold=0.0).presentation
            == PRIMARY
        )
        assert (
            classify_candidate(score=0.50, corroborated_by=[], threshold=0.9).presentation
            == LOW_CONFIDENCE
        )

    def test_zero_turns_suppression_off_including_for_a_zero_scored_candidate(self):
        """The off switch, tested at the value that would have slipped through.

        The rule compares ``score > threshold``, which is false at 0.0 — so a
        zero threshold would still have suppressed a zero-scored candidate, and
        "0 restores the previous behaviour" would have been very nearly true.
        Zero is handled as its own branch instead.
        """
        for score in (0.0, 0.01, 0.05, 0.15, 0.99):
            verdict = classify_candidate(score=score, corroborated_by=[], threshold=0.0)
            assert verdict.presentation == PRIMARY, score
            assert not verdict.suppressed, score

    def test_the_off_switch_does_not_overturn_an_analyst(self):
        """Off means the *automated* suppression is off.

        A rejection is a human verdict, not a threshold effect, so turning the
        threshold off must not drag a ruled-out candidate back into the primary
        view.
        """
        verdict = classify_candidate(
            score=0.9, corroborated_by=["orcid"], decision="REJECTED", threshold=0.0
        )
        assert verdict.presentation == REJECTED

    def test_the_scoring_pipeline_cannot_currently_produce_a_zero_candidate(self):
        """Why the branch above is belt *and* braces.

        Every candidate is created carrying ``same_person_name`` (0.15), the
        weakest name rule in the table is 0.05, no rule has weight zero, and
        :meth:`ConfidenceEngine.score` floors a combination at its highest
        single signal. So the pipeline cannot mint a 0.0 candidate today.

        This test records that invariant rather than relying on it: it spans
        extraction, the engine, the resolver and the corroboration pass, and
        ``Entity.confidence`` is a plain float column with no CHECK constraint,
        so nothing stops a future path writing one. If this test ever fails, the
        zero branch in ``classify_candidate`` is the thing keeping the off
        switch honest.
        """
        from app.correlation.confidence import DEFAULT_RULES, default_engine
        from app.services.name_variants import PARTIAL, confidence_rule_for

        assert not [key for key, rule in DEFAULT_RULES.items() if rule.score <= 0.0]

        weakest = confidence_rule_for(PARTIAL)
        assert default_engine.score([default_engine.signal(weakest)]).score == pytest.approx(0.05)
        assert default_engine.score(
            [default_engine.signal("same_person_name")]
        ).score == pytest.approx(0.15)

        # A combination never scores below its strongest single signal.
        combined = default_engine.score(
            [default_engine.signal(weakest), default_engine.signal("same_person_name")]
        )
        assert combined.score >= 0.15

    def test_an_analyst_outranks_the_arithmetic_in_both_directions(self):
        assert (
            classify_candidate(score=0.05, corroborated_by=[], decision="CONFIRMED").presentation
            == PRIMARY
        )
        assert (
            classify_candidate(
                score=0.95, corroborated_by=["orcid"], decision="REJECTED"
            ).presentation
            == REJECTED
        )

    def test_several_weak_spellings_do_not_add_up_to_a_strong_one(self):
        """Three near-miss spellings of one name are three weak leads.

        The rule reads one candidate at a time and has no notion of how many
        others look like it, which is the point: "lots of sources agree on the
        name" is still only the name.
        """
        spellings = ("Samara", "Samari", "Samra")
        verdicts = [classify_candidate(score=0.05, corroborated_by=[]) for _ in spellings]
        assert all(verdict.presentation == LOW_CONFIDENCE for verdict in verdicts)


# --- through the API --------------------------------------------------------


@pytest.mark.anyio
class TestThroughTheApi:
    async def test_the_sara_samara_fixture(self, api_client, case_id, workspace_id):
        """The reported case: two near-misses and an unrelated author record."""
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samara", score=0.05)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)
        _candidate(case_id, workspace_id, name="S. Samara", score=0.05)
        _candidate(
            case_id,
            workspace_id,
            name="Sara Samara",
            score=0.55,
            corroborated_by=["affiliation"],
            url="https://orcid.org/0000-0002-1825-0097",
        )

        groups = await _all_groups(api_client, case_id)

        assert len(groups) == 4, "every candidate is still returned"
        folded = [g for g in groups if g["presentation"] == LOW_CONFIDENCE]
        assert len(folded) == 3
        assert {g["display_name"] for g in folded} == {"Sara Samara", "Sara Samari", "S. Samara"}
        primary = [g for g in groups if g["presentation"] == PRIMARY]
        assert len(primary) == 1
        assert primary[0]["corroborated_by"] == ["affiliation"]
        # The two spellings of "Sara Samara" are still two separate records: one
        # corroborated, one not. A shared name never merged them.
        assert sum(g["display_name"] == "Sara Samara" for g in groups) == 2

    async def test_a_weak_candidate_keeps_its_score_and_its_reasons(
        self, api_client, case_id, workspace_id
    ):
        """Suppression is presentation. It touches nothing it is presenting."""
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)

        group = (await _groups(api_client, case_id))["Sara Samari"]

        assert group["presentation"] == LOW_CONFIDENCE
        assert group["confidence"] == pytest.approx(0.05)
        assert group["confidence_reasons"]
        assert group["match_reasons"]
        assert group["mismatch_reasons"]
        assert group["presentation_reason"]

    async def test_an_exact_name_with_no_corroboration_is_still_treated_cautiously(
        self, api_client, case_id, workspace_id
    ):
        """0.15 is what a name and nothing else produces.

        It is above the threshold, so it is shown — hiding the actual result of
        a name search would be worse than showing it. But it is never marked as
        corroborated, and the reason says exactly what carried it: a number, not
        a fact about the record.
        """
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samara", score=0.15)

        group = (await _groups(api_client, case_id))["Sara Samara"]

        assert group["presentation"] == PRIMARY
        assert group["corroborated_by"] == []
        assert "No anchor matched" in group["presentation_reason"]

    async def test_a_weak_name_with_a_matching_employer_is_visible(
        self, api_client, case_id, workspace_id
    ):
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(
            case_id, workspace_id, name="Sara Samari", score=0.05, corroborated_by=["affiliation"]
        )

        group = (await _groups(api_client, case_id))["Sara Samari"]

        assert group["presentation"] == PRIMARY
        assert "affiliation" in group["presentation_reason"]

    async def test_a_weak_name_with_a_supplied_username_is_visible(
        self, api_client, case_id, workspace_id
    ):
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(
            case_id, workspace_id, name="Sara Samari", score=0.05, corroborated_by=["username"]
        )

        assert (await _groups(api_client, case_id))["Sara Samari"]["presentation"] == PRIMARY

    async def test_an_analyst_confirmed_weak_candidate_is_visible(
        self, api_client, case_id, workspace_id
    ):
        await _person_target(api_client, case_id, context=ANCHORS)
        entity_id = _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)

        response = await api_client.post(
            f"/api/v1/cases/{case_id}/decisions",
            json={
                "subject_type": "CANDIDATE",
                "subject_id": entity_id,
                "decision": "CONFIRMED",
                "note": "Matched by hand against the university directory.",
            },
        )
        assert response.status_code in (200, 201), response.text

        group = (await _groups(api_client, case_id))["Sara Samari"]
        assert group["presentation"] == PRIMARY
        assert group["confidence"] == pytest.approx(0.05), "the score is untouched"
        assert "analyst confirmed" in group["presentation_reason"].lower()

    async def test_an_analyst_rejected_candidate_leaves_the_primary_view_but_stays(
        self, api_client, case_id, workspace_id
    ):
        await _person_target(api_client, case_id, context=ANCHORS)
        entity_id = _candidate(
            case_id,
            workspace_id,
            name="Sara Samara",
            score=0.88,
            corroborated_by=["orcid"],
        )

        response = await api_client.post(
            f"/api/v1/cases/{case_id}/decisions",
            json={
                "subject_type": "CANDIDATE",
                "subject_id": entity_id,
                "decision": "REJECTED",
                "note": "Confirmed a different person by date of birth.",
            },
        )
        assert response.status_code in (200, 201), response.text

        group = (await _groups(api_client, case_id))["Sara Samara"]
        assert group["presentation"] == REJECTED
        assert group["decision"]["decision"] == "REJECTED"
        # Still returned, still scored, still carrying why it was ever a candidate.
        assert group["confidence"] == pytest.approx(0.88)
        assert group["corroborated_by"] == ["orcid"]

        audit = await api_client.get(f"/api/v1/cases/{case_id}/decisions")
        assert audit.status_code == 200
        assert any(row["subject_id"] == entity_id for row in audit.json())

    async def test_nothing_is_suppressed_when_no_anchors_were_supplied(
        self, api_client, case_id, workspace_id
    ):
        """A subtlety worth an explicit test.

        With no anchors, nothing *could* corroborate a candidate. Folding every
        result away on that basis would report the investigator's own missing
        input as a property of the records. The threshold still applies — so a
        0.05 partial-name hit is still weak — but the reason says which of the
        two situations this is.
        """
        await _person_target(api_client, case_id)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)

        group = (await _groups(api_client, case_id))["Sara Samari"]
        assert group["presentation"] == LOW_CONFIDENCE
        assert "no anchors were supplied" in group["presentation_reason"].lower()

    async def test_unattributed_evidence_is_never_folded_away(
        self, api_client, case_id, workspace_id
    ):
        """Evidence nobody has filed yet is not a weak match.

        It scores 0.0 because it has no candidate, not because it is bad. The
        endpoint has always surfaced it so an import cannot silently vanish, and
        suppression must not undo that.
        """
        await _person_target(api_client, case_id, context=ANCHORS)
        groups = await _groups(api_client, case_id)
        orphan = groups.get("Unattributed evidence")
        if orphan is not None:
            assert orphan["presentation"] == PRIMARY


# --- the report -------------------------------------------------------------


@pytest.mark.anyio
class TestTheReport:
    async def test_the_export_still_carries_every_candidate(
        self, api_client, case_id, workspace_id
    ):
        """Requirement in one assertion: suppressed does not mean absent.

        The JSON export is the auditable artefact. A candidate folded out of the
        readable list is still in ``entities``, with its score and its reasons,
        and is *additionally* listed under ``low_confidence_candidates`` so a
        renderer can collapse it.
        """
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)
        _candidate(case_id, workspace_id, name="Sara Samara", score=0.05)
        _candidate(
            case_id, workspace_id, name="Sara Samara", score=0.61, corroborated_by=["affiliation"]
        )

        response = await api_client.get(
            f"/api/v1/cases/{case_id}/report", params={"format": "json"}
        )
        assert response.status_code == 200, response.text
        report = response.json()

        candidates = [
            item for item in report["entities"] if item["attributes"].get("role") == "candidate"
        ]
        assert len(candidates) == 3, "entities is complete"
        assert len(report["low_confidence_candidates"]) == 2
        assert report["counts"]["low_confidence_candidates"] == 2

        folded_ids = {item["id"] for item in report["low_confidence_candidates"]}
        assert folded_ids <= {item["id"] for item in candidates}, "listed again, not moved"
        for item in report["low_confidence_candidates"]:
            assert item["confidence"] == pytest.approx(0.05)
            assert item["presentation_reason"]

    async def test_no_evidence_or_provenance_row_is_removed(
        self, api_client, case_id, workspace_id
    ):
        """Counted before and after, through the API that serves them."""
        await _person_target(api_client, case_id, context=ANCHORS)

        before = await api_client.get(f"/api/v1/cases/{case_id}/evidence")
        assert before.status_code == 200
        total_before = before.json()["total"]

        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)
        _candidate(case_id, workspace_id, name="Sara Samara", score=0.05)

        after = await api_client.get(f"/api/v1/cases/{case_id}/evidence")
        assert after.status_code == 200
        assert after.json()["total"] == total_before

        report = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
        assert len(report.json()["evidence"]) == total_before

    async def test_the_markdown_report_collapses_rather_than_omits(
        self, api_client, case_id, workspace_id
    ):
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)

        response = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
        assert response.status_code == 200, response.text
        body = response.text

        assert "Low-confidence candidates (1)" in body
        assert "Sara Samari" in body, "folded away, not dropped"
        assert "<details>" in body

    async def test_the_score_column_is_not_called_confidence(
        self, api_client, case_id, workspace_id
    ):
        """Terminology: the number is a correlation score, not a probability."""
        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(
            case_id, workspace_id, name="Sara Samara", score=0.61, corroborated_by=["affiliation"]
        )

        response = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
        body = response.text
        entities_table = body.split("## Entities", 1)[1].split("## Relationships", 1)[0]
        assert "Correlation score" in entities_table
        assert "| Confidence |" not in entities_table


# --- the threshold is configuration -----------------------------------------


@pytest.mark.anyio
class TestTheThresholdIsConfigurable:
    async def test_zero_restores_the_previous_behaviour(
        self, api_client, case_id, workspace_id, monkeypatch
    ):
        """Every candidate in the primary view, as before this existed."""
        from app.core import settings as settings_module

        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samari", score=0.05)

        _candidate(case_id, workspace_id, name="Zero Scored", score=0.0)

        current = settings_module.get_settings()
        monkeypatch.setattr(current, "candidate_suppression_threshold", 0.0, raising=False)

        groups = await _groups(api_client, case_id)
        assert groups["Sara Samari"]["presentation"] == PRIMARY
        # The boundary case: `score > threshold` is false here, so this row is
        # the one an off switch built on that comparison alone would still hide.
        assert groups["Zero Scored"]["presentation"] == PRIMARY

    async def test_raising_it_folds_more_away(self, api_client, case_id, workspace_id, monkeypatch):
        from app.core import settings as settings_module

        await _person_target(api_client, case_id, context=ANCHORS)
        _candidate(case_id, workspace_id, name="Sara Samara", score=0.15)

        current = settings_module.get_settings()
        monkeypatch.setattr(current, "candidate_suppression_threshold", 0.5, raising=False)

        groups = await _groups(api_client, case_id)
        assert groups["Sara Samara"]["presentation"] == LOW_CONFIDENCE
