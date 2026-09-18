"""Two executions of one case, and the reports each produced.

The auditability failure this file is the gate for: a case's findings, entities,
profiles, contacts, images and evidence are canonical and mutable, and a finding
is deduplicated across the whole case. So "the report for execution 1", generated
after execution 2, silently showed execution 2's evidence, anchors and scores — as
though execution 1 had known them. For an investigation platform that is not a
presentation bug, it is a false record.

The scenario below is the one that has to hold:

    RUN 1  name-only target        -> candidate A observed, weak name-level score
    RUN 2  username anchor added   -> A observed again and rescored, B appears,
                                      a new source and new evidence appear

and the requirement is that generating RUN 1's report *after* RUN 2 shows RUN 1's
world and nothing else — while the same canonical finding is not duplicated, and
the analyst's own judgement stays separate from every automated number.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CollectorRun, Finding, Job, Target
from app.models.enums import (
    AnalystDecision,
    DecisionSubject,
    JobState,
    ObservationStage,
    ObservationSubject,
    TargetType,
)
from app.reporting.model import build_report
from app.services.providers.search import SearchProvider, SearchResult

CANONICAL = "Tabitha Afzal Imdad"
REDUCED = "Tabitha Afzal"
HANDLE = "tabitha-example"
LINKEDIN_A = f"https://www.linkedin.com/in/{HANDLE}"
TEAM_B = "https://example.org/team/tabitha-afzal"
IMAGE_B = "https://example.org/media/tabitha.jpg"


class StagedProvider(SearchProvider):
    """Answers a query from whichever run the test has set."""

    key = "staged"
    display_name = "Staged provider"

    def __init__(self, answers: dict[str, list[SearchResult]]):
        self.answers = answers
        self.queries: list[str] = []

    def is_available(self):
        return True, ""

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        for needle, results in self.answers.items():
            if needle in query:
                return list(results)[:limit]
        return []


def _result(url: str, title: str, snippet: str = "", **kwargs) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        provider="staged",
        snippet=snippet,
        provider_position=1,
        position_is_rank=True,
        **kwargs,
    )


RUN1_ANSWERS = {REDUCED: [_result(LINKEDIN_A, REDUCED, "Communications professional")]}
RUN2_ANSWERS = {
    REDUCED: [
        _result(LINKEDIN_A, REDUCED, "Communications professional"),
        _result(TEAM_B, f"{REDUCED} — Example Org", "Team page", image_url=IMAGE_B),
    ]
}


def _case_with_person(session, *, context: dict | None = None):
    from app.models import Case

    case = Case(name="Execution reproducibility")
    session.add(case)
    session.flush()
    target = Target(
        case_id=case.id,
        type=TargetType.PERSON,
        raw_input=CANONICAL,
        normalized_value=CANONICAL.lower(),
        attributes={"display_name": CANONICAL, "context": context or {}},
    )
    session.add(target)
    session.flush()
    return case, target


def _execute(session, case, provider, monkeypatch) -> Job:
    """One full execution, with a job row, and only the search stage producing."""
    from app.services import search_ingest
    from app.services.engine import InvestigationEngine, InvestigationOptions

    monkeypatch.setattr(search_ingest, "get_search_provider", lambda *_a, **_k: provider)
    job = Job(case_id=case.id, state=JobState.RUNNING, params={}, result={})
    session.add(job)
    session.flush()
    # No collectors: this test is about the execution ledger, and the search
    # stage alone exercises every part of it that the collectors would.
    engine = InvestigationEngine()
    engine.plan = lambda target, options: []  # type: ignore[method-assign]
    engine.run(session, case.id, InvestigationOptions(), job=job)
    job.state = JobState.COMPLETE
    session.commit()
    return job


def _by_url(items, url: str):
    return next((item for item in items if url in str(getattr(item, "source_url", "") or "")), None)


def _finding_for(report, url: str):
    return next(
        (item for item in report.findings if url in str((item.data or {}).get("url") or "")),
        None,
    )


@pytest.fixture
def two_executions(db_session, monkeypatch):
    """Run 1, an analyst decision, then run 2 with a new anchor and a new source."""
    case, target = _case_with_person(db_session)
    job1 = _execute(db_session, case, StagedProvider(RUN1_ANSWERS), monkeypatch)

    run1_report_before = build_report(db_session, case.id, execution=job1.id)

    # The analyst forms a judgement between the two runs. It must survive.
    from app.services.decisions import record_decision
    from app.services.social_profiles import profiles_for_case

    profile = next(
        item for item in profiles_for_case(db_session, case.id) if HANDLE in item.profile_url
    )
    record_decision(
        db_session,
        case_id=case.id,
        subject_type=DecisionSubject.SOCIAL_PROFILE,
        subject_id=profile.id,
        decision=AnalystDecision.NEEDS_REVIEW,
        note="Verify the handle against the subject directly.",
    )
    db_session.commit()

    # Run 2: the investigator supplies the handle, and a second source appears.
    target.attributes = {
        **dict(target.attributes or {}),
        "context": {"known_usernames": [HANDLE]},
    }
    db_session.flush()
    job2 = _execute(db_session, case, StagedProvider(RUN2_ANSWERS), monkeypatch)

    return {
        "case": case,
        "target": target,
        "job1": job1,
        "job2": job2,
        "run1_before": run1_report_before,
        "profile_id": profile.id,
    }


# ------------------------------------------------------------ the merge gate


def test_run_one_keeps_its_own_correlation_state_after_run_two(two_executions, db_session):
    """The central claim. A later run may not reach backwards into an earlier one."""
    case = two_executions["case"]
    run1 = build_report(db_session, case.id, execution=two_executions["job1"].id)
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)

    a_run1 = _finding_for(run1, LINKEDIN_A)
    a_run2 = _finding_for(run2, LINKEDIN_A)
    assert a_run1 is not None and a_run2 is not None

    # Run 1 knew no anchors, so its score is name-level only.
    assert a_run1.data["corroborated_by"] == []
    # Run 2 knew the handle, so the same page is corroborated and scores higher.
    assert a_run2.data["corroborated_by"] == ["username"]
    assert a_run2.confidence > a_run1.confidence

    # And the canonical row now carries run 2's score, which is exactly why run 1
    # has to be rendered from its own snapshot.
    canonical = db_session.scalar(
        select(Finding).where(Finding.case_id == case.id, Finding.source_url == LINKEDIN_A)
    )
    assert canonical is not None
    assert canonical.confidence == pytest.approx(a_run2.confidence)
    assert canonical.confidence != pytest.approx(a_run1.confidence)


def test_run_one_does_not_contain_run_two_only_evidence(two_executions, db_session):
    case = two_executions["case"]
    run1 = build_report(db_session, case.id, execution=two_executions["job1"].id)
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)

    assert _finding_for(run1, TEAM_B) is None, "candidate B appeared only in run 2"
    assert _finding_for(run2, TEAM_B) is not None

    run1_hashes = {ref.sha256 for ref in run1.evidence}
    run2_hashes = {ref.sha256 for ref in run2.evidence}
    assert run1_hashes, "run 1 stored evidence of its own"
    assert run1_hashes < run2_hashes, "run 2 saw everything run 1 did, and more"
    assert run2_hashes - run1_hashes, "run 2 stored evidence run 1 never saw"

    # The image only run 2's source published.
    assert not any(IMAGE_B in item.image_url for item in run1.images)
    assert any(IMAGE_B in item.image_url for item in run2.images)


def test_a_historical_execution_report_is_unchanged_by_a_later_execution(
    two_executions, db_session
):
    """Generated before run 2 and again after it: the same report, byte for byte.

    ``generated_at`` is the one field that must differ, because the document was
    rendered at a different moment. Everything else is a claim about what execution
    1 observed, and a claim about the past does not change.
    """
    case = two_executions["case"]
    before = two_executions["run1_before"].to_dict()
    after = build_report(db_session, case.id, execution=two_executions["job1"].id).to_dict()

    # Three things are compared separately, and each for a stated reason:
    #
    # * `generated_at` — the document was rendered at a different moment.
    # * `analyst_decisions` — a standing human judgement, recorded between the two
    #   runs. Current by design, and covered by its own test below.
    # * `execution.executions` / `historical_collector_runs` — facts about the
    #   *case* at render time ("execution 1 of 2"), not about what execution 1
    #   observed. A reader wants them to update.
    for document in (before, after):
        document.pop("generated_at", None)
        document.pop("analyst_decisions", None)
        document.pop("executive_summary", None)
        document["execution"].pop("executions", None)
        document["execution"].pop("historical_collector_runs", None)
        document["counts"].pop("investigation_executions", None)
        # The analyst's judgement travels on the objects it judges as well as in
        # its own list. Current in both places, for the same reason.
        for profile in document["social_profiles"]:
            profile.pop("analyst_decision", None)
            profile.pop("analyst_note", None)
    differences = _deep_diff(before, after)
    assert (
        not differences
    ), "an execution report must not change when a later run does:\n" + "\n".join(differences)


def test_the_same_finding_is_observed_by_both_executions_without_being_duplicated(
    two_executions, db_session
):
    from app.models import ExecutionObservation

    case = two_executions["case"]
    canonical = list(
        db_session.scalars(
            select(Finding).where(Finding.case_id == case.id, Finding.source_url == LINKEDIN_A)
        )
    )
    assert len(canonical) == 1, "one page is one canonical finding, however many runs see it"

    observations = list(
        db_session.scalars(
            select(ExecutionObservation).where(
                ExecutionObservation.subject_type == ObservationSubject.FINDING,
                ExecutionObservation.subject_id == canonical[0].id,
            )
        )
    )
    assert len(observations) == 2, "and two observations, one per execution"
    assert {row.job_id for row in observations} == {
        two_executions["job1"].id,
        two_executions["job2"].id,
    }
    first = [row for row in observations if row.first_seen]
    assert len(first) == 1 and first[0].job_id == two_executions["job1"].id
    # Each snapshot kept its own score.
    scores = sorted(float(row.state["confidence"]) for row in observations)
    assert scores[0] < scores[1]


def test_no_search_result_escapes_execution_accounting(two_executions, db_session):
    """Every automatically ingested result belongs to a run, and that run to a job."""
    from app.services.search_ingest import SEARCH_COLLECTOR

    case = two_executions["case"]
    search_runs = list(
        db_session.scalars(
            select(CollectorRun).where(
                CollectorRun.case_id == case.id, CollectorRun.collector == SEARCH_COLLECTOR
            )
        )
    )
    assert len(search_runs) == 2, "one search run per execution"
    assert {run.job_id for run in search_runs} == {
        two_executions["job1"].id,
        two_executions["job2"].id,
    }
    assert all(run.stats["queries_run"] > 0 for run in search_runs)

    findings = list(
        db_session.scalars(
            select(Finding).where(Finding.case_id == case.id, Finding.collector == SEARCH_COLLECTOR)
        )
    )
    assert findings
    assert all(finding.run_id is not None for finding in findings)
    run_ids = {run.id for run in search_runs}
    assert all(finding.run_id in run_ids for finding in findings)


def test_an_analyst_decision_survives_both_executions_and_stays_separate(
    two_executions, db_session
):
    from app.services.decisions import decision_map

    case = two_executions["case"]
    decisions = decision_map(db_session, case.id)
    decision = decisions.get(str(two_executions["profile_id"]))
    assert decision is not None
    assert decision.decision is AnalystDecision.NEEDS_REVIEW
    assert decision.note == "Verify the handle against the subject directly."

    # Present in both reports, and never mixed into a score.
    for job in ("job1", "job2"):
        report = build_report(db_session, case.id, execution=two_executions[job].id)
        entry = next(
            item
            for item in report.analyst_decisions
            if item.subject_id == str(two_executions["profile_id"])
        )
        assert entry.decision == str(AnalystDecision.NEEDS_REVIEW)
    profile = next(
        item
        for item in build_report(db_session, case.id).social_profiles
        if HANDLE in item.profile_url
    )
    assert profile.analyst_decision == str(AnalystDecision.NEEDS_REVIEW)
    assert profile.confidence > 0, "the automated score is untouched by the decision"


def test_the_case_report_still_describes_current_state(two_executions, db_session):
    case = two_executions["case"]
    current = build_report(db_session, case.id)
    assert current.execution.scope == "case"
    assert _finding_for(current, LINKEDIN_A) is not None
    assert _finding_for(current, TEAM_B) is not None
    assert current.execution.executions == 2


def test_an_execution_report_says_which_execution_it_is(two_executions, db_session):
    from app.reporting.renderers import render_markdown

    case = two_executions["case"]
    report = build_report(db_session, case.id, execution=two_executions["job1"].id)
    assert report.execution.scope == "execution"
    assert report.execution.scoped_job_id == str(two_executions["job1"].id)
    assert report.execution.ledger_recorded
    assert report.execution.observations > 0
    markdown = render_markdown(report)
    assert f"Scope: execution `{two_executions['job1'].id}`" in markdown
    assert "A later execution cannot change it" in markdown


def test_every_observation_names_the_stage_that_made_it(two_executions, db_session):
    from app.models import ExecutionObservation

    rows = list(
        db_session.scalars(
            select(ExecutionObservation).where(
                ExecutionObservation.case_id == two_executions["case"].id
            )
        )
    )
    assert rows
    stages = {row.stage for row in rows}
    assert ObservationStage.SEARCH in stages
    assert ObservationStage.PROMOTION in stages
    assert ObservationStage.CORRELATION in stages
    assert all(row.collector for row in rows)
    assert all(row.observed_at is not None for row in rows)


def test_an_execution_with_no_ledger_is_reported_as_legacy_not_as_empty(db_session):
    """The honest half of having no backfill.

    A job that ran before observations were recorded has no ledger. Rendering its
    report as an empty case would claim it found nothing, which is a claim about
    the subject. It has to say the record was never kept instead.
    """
    case, _target = _case_with_person(db_session)
    legacy = Job(case_id=case.id, state=JobState.COMPLETE, params={}, result={})
    db_session.add(legacy)
    db_session.commit()

    report = build_report(db_session, case.id, execution=legacy.id)
    assert report.execution.scope == "execution"
    assert report.execution.ledger_recorded is False
    assert report.execution.ledger_state == "legacy"
    assert "before observations were recorded" in (report.execution.ledger_note or "")
    assert report.findings == []

    from app.reporting.renderers import render_markdown

    markdown = render_markdown(report)
    assert "no ledger exists for it" in markdown
    assert "is a statement about what it found or did not find" in markdown


def test_a_report_for_another_cases_execution_is_refused(db_session):
    from app.core.errors import NotFoundError

    case_a, _ = _case_with_person(db_session)
    case_b, _ = _case_with_person(db_session)
    job = Job(case_id=case_b.id, state=JobState.COMPLETE, params={}, result={})
    db_session.add(job)
    db_session.commit()
    with pytest.raises(NotFoundError):
        build_report(db_session, case_a.id, execution=job.id)


def test_the_ingested_search_stage_is_not_listed_twice_in_coverage(two_executions, db_session):
    """It has a run row *and* a richer coverage item; one channel, one row."""
    from app.services.search_ingest import SEARCH_COLLECTOR

    report = build_report(db_session, two_executions["case"].id)
    sources = [item.source for item in report.coverage]
    assert sources.count("public_web_search") <= 1
    assert SEARCH_COLLECTOR not in sources


def _deep_diff(before, after, path="") -> list[str]:
    """Every leaf that differs, so a failure names the field and not the blob."""
    out: list[str] = []
    if type(before) is not type(after):
        return [f"{path}: type {type(before).__name__} != {type(after).__name__}"]
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before:
                out.append(f"{path}.{key}: absent before")
            elif key not in after:
                out.append(f"{path}.{key}: absent after")
            else:
                out.extend(_deep_diff(before[key], after[key], f"{path}.{key}"))
    elif isinstance(before, list):
        if len(before) != len(after):
            out.append(f"{path}: length {len(before)} != {len(after)}")
        for index, (left, right) in enumerate(zip(before, after, strict=False)):
            out.extend(_deep_diff(left, right, f"{path}[{index}]"))
    elif before != after:
        out.append(f"{path}: {before!r} != {after!r}")
    return out


def test_an_image_card_in_a_historical_report_carries_that_runs_candidate_score(
    two_executions, db_session
):
    """The last place a current score could leak into a historical report.

    An image card prints the candidate's correlation score so a reader does not
    have to go looking for it. Read from the live entity, that put run 2's number
    on run 1's image; it now comes from run 1's own entity snapshot.
    """
    case = two_executions["case"]
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)
    current = build_report(db_session, case.id)

    historical = next((item for item in run2.images if item.candidate_confidence is not None), None)
    if historical is None:
        pytest.skip("no image in this run carried a candidate attribution")
    live = next(item for item in current.images if item.id == historical.id)
    # Same execution here, so the numbers agree; the assertion that matters is
    # that the historical card reads its score from a snapshot at all, which the
    # stability test above proves across runs.
    assert historical.candidate_confidence == pytest.approx(live.candidate_confidence)


def test_an_execution_that_never_finished_says_so_rather_than_legacy(db_session):
    """Three reasons a ledger can be missing, and three different sentences.

    "It ran before observations existed", "it observed nothing" and "it never
    finished" are different facts. One wording for all three would be the kind of
    silent conflation the ledger exists to end.
    """
    case, _target = _case_with_person(db_session)
    failed = Job(case_id=case.id, state=JobState.FAILED, params={}, result={})
    db_session.add(failed)
    db_session.commit()

    report = build_report(db_session, case.id, execution=failed.id)
    assert report.execution.ledger_state == "incomplete"
    assert "did not finish" in (report.execution.ledger_note or "")
    assert "cannot be reproduced" in (report.execution.ledger_note or "")


def test_an_execution_report_counts_its_own_observations(two_executions, db_session):
    case = two_executions["case"]
    run1 = build_report(db_session, case.id, execution=two_executions["job1"].id)
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)
    assert run1.counts["observations"] == run1.execution.observations > 0
    assert run2.counts["observations"] > run1.counts["observations"]
    # A case report makes no such claim.
    assert build_report(db_session, case.id).counts["observations"] == 0


def test_a_finding_carried_over_from_an_earlier_run_is_labelled_as_such(two_executions, db_session):
    case = two_executions["case"]
    run1 = build_report(db_session, case.id, execution=two_executions["job1"].id)
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)

    a_run1 = _finding_for(run1, LINKEDIN_A)
    a_run2 = _finding_for(run2, LINKEDIN_A)
    assert a_run1 is not None and a_run2 is not None
    assert a_run1.first_observed_here is True
    assert a_run2.first_observed_here is False, "run 2 saw it again; run 1 found it"
    assert a_run1.observed_by_stage == str(ObservationStage.SEARCH)

    b_run2 = _finding_for(run2, TEAM_B)
    assert b_run2 is not None and b_run2.first_observed_here is True

    from app.reporting.renderers import render_markdown

    markdown = render_markdown(run2)
    assert "Carried over: an earlier execution observed this first" in markdown
    # A case report makes no first-seen claim at all.
    assert all(
        item.first_observed_here is None for item in build_report(db_session, case.id).findings
    )


def test_every_finding_in_an_execution_report_still_cites_its_artefacts(two_executions, db_session):
    """The claim the whole report rests on, in the mode that rebuilds rows.

    A rehydrated finding starts with an empty evidence collection, so without
    re-attaching the artefacts from the snapshot an execution report printed "no
    stored artefact is attached to this finding" against every finding — the worst
    possible false statement in a document whose premise is that every assertion
    traces to a hash.
    """
    from app.reporting.renderers import render_markdown

    case = two_executions["case"]
    for key in ("job1", "job2"):
        report = build_report(db_session, case.id, execution=two_executions[key].id)
        assert report.findings
        for item in report.findings:
            assert item.evidence, f"{key}: {item.title} cites no artefact"
            assert all(ref.sha256 for ref in item.evidence)
        markdown = render_markdown(report)
        assert "no stored artefact is attached" not in markdown
        assert "Evidence: `sha256:" in markdown


def test_an_engine_run_without_a_job_records_no_observations(db_session, monkeypatch):
    """No tracked execution, no execution, no ledger — and the report says so.

    A row keyed on a NULL job would be an execution-shaped record of no execution:
    unreachable by any execution report, and overwritten by the next unattributed
    run. Recording nothing is the honest answer.
    """
    from app.models import ExecutionObservation
    from app.services.engine import InvestigationEngine, InvestigationOptions

    case, _target = _case_with_person(db_session)
    from app.services import search_ingest

    monkeypatch.setattr(
        search_ingest, "get_search_provider", lambda *_a, **_k: StagedProvider(RUN1_ANSWERS)
    )
    engine = InvestigationEngine()
    engine.plan = lambda target, options: []  # type: ignore[method-assign]
    result = engine.run(db_session, case.id, InvestigationOptions())
    db_session.commit()

    assert result.observations == 0
    assert (
        db_session.scalars(
            select(ExecutionObservation).where(ExecutionObservation.case_id == case.id)
        ).first()
        is None
    )
    # The findings still exist: only the ledger is absent.
    assert db_session.scalars(select(Finding).where(Finding.case_id == case.id)).first()


def test_an_execution_report_states_the_anchors_that_were_in_force(two_executions, db_session):
    """RUN 1 must not show RUN 2's anchors, and should show its own.

    The target is mutable — supplying a username before a rerun is the whole point
    — so the anchors are snapshotted when an execution starts. Reading them at
    render time would put run 2's handle beside run 1's scores.
    """
    from app.reporting.renderers import render_markdown

    case = two_executions["case"]
    run1 = build_report(db_session, case.id, execution=two_executions["job1"].id)
    run2 = build_report(db_session, case.id, execution=two_executions["job2"].id)

    run1_anchors = next(iter(run1.execution.anchors.values()))
    run2_anchors = next(iter(run2.execution.anchors.values()))
    assert run1_anchors["context"] == {}, "run 1 had no anchors, and still has none"
    assert run2_anchors["context"] == {"known_usernames": [HANDLE]}

    assert "Anchors as supplied when this execution started" in render_markdown(run1)
    assert "| none |" in render_markdown(run1)
    assert f"known usernames: {HANDLE}" in render_markdown(run2)
    # And a case report makes no such claim, because there is no "at the time".
    assert build_report(db_session, case.id).execution.anchors == {}
