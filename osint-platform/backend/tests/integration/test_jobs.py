"""Job lifecycle, progress and cancellation."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.models.enums import JobState
from app.schemas.case import CaseCreate, TargetCreate
from app.services import cases as case_service
from app.services import jobs as job_service


@pytest.fixture
def case(db_session):
    created = case_service.create_case(db_session, CaseCreate(name="Job test"))
    case_service.add_target(db_session, created.id, TargetCreate(value="example.com"))
    db_session.commit()
    return created


def test_created_job_starts_queued(db_session, case):
    job = job_service.create_job(db_session, case.id)
    assert job.state is JobState.QUEUED
    assert job.progress == 0.0
    assert job.cancel_requested is False
    assert job.params["include_collectors"] == []


def test_job_records_its_options(db_session, case):
    job = job_service.create_job(
        db_session, case.id, include_collectors=["dns"], exclude_collectors=["rdap"]
    )
    assert job.params["include_collectors"] == ["dns"]
    assert job.params["exclude_collectors"] == ["rdap"]


def test_only_one_active_job_per_case(db_session, case):
    job_service.create_job(db_session, case.id)
    with pytest.raises(ConflictError, match="already has an active investigation"):
        job_service.create_job(db_session, case.id)


def test_a_finished_job_frees_the_case(db_session, case):
    from app.services.engine import InvestigationResult

    first = job_service.create_job(db_session, case.id)
    job_service.finish_job(db_session, first.id, InvestigationResult(case_id=case.id))
    second = job_service.create_job(db_session, case.id)
    assert second.id != first.id


def test_unknown_case_and_job_raise(db_session):
    with pytest.raises(NotFoundError):
        job_service.create_job(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        job_service.get_job(db_session, uuid.uuid4())


def test_progress_updates_move_the_job_to_running(db_session, case):
    job = job_service.create_job(db_session, case.id)
    job_service.update_progress(db_session, job.id, 0.5, "Halfway")
    refreshed = job_service.get_job(db_session, job.id)
    assert refreshed.state is JobState.RUNNING
    assert refreshed.progress == 0.5
    assert refreshed.message == "Halfway"


def test_progress_is_clamped(db_session, case):
    job = job_service.create_job(db_session, case.id)
    job_service.update_progress(db_session, job.id, 5.0, "Overshoot")
    assert job_service.get_job(db_session, job.id).progress == 1.0


def test_progress_for_an_unknown_job_is_silent(db_session):
    job_service.update_progress(db_session, uuid.uuid4(), 0.5, "nobody")


def test_cancelling_a_queued_job_finalises_it(db_session, case):
    job = job_service.create_job(db_session, case.id)
    cancelled = job_service.request_cancel(db_session, job.id)
    assert cancelled.state is JobState.CANCELLED
    assert cancelled.cancel_requested is True
    assert cancelled.finished_at is not None


def test_cancelling_a_running_job_sets_the_flag(db_session, case):
    job = job_service.create_job(db_session, case.id)
    job_service.start_job(db_session, job.id)
    cancelled = job_service.request_cancel(db_session, job.id)
    assert cancelled.state is JobState.RUNNING
    assert cancelled.cancel_requested is True
    assert job_service.is_cancelled(db_session, job.id) is True


def test_cancelling_a_finished_job_conflicts(db_session, case):
    from app.services.engine import InvestigationResult

    job = job_service.create_job(db_session, case.id)
    job_service.finish_job(db_session, job.id, InvestigationResult(case_id=case.id))
    with pytest.raises(ConflictError, match="already finished"):
        job_service.request_cancel(db_session, job.id)


def test_finish_records_the_result(db_session, case):
    from app.services.engine import InvestigationResult

    job = job_service.create_job(db_session, case.id)
    result = InvestigationResult(case_id=case.id, findings_created=4, entities_created=2)
    finished = job_service.finish_job(db_session, job.id, result)
    assert finished.state is JobState.COMPLETE
    assert finished.progress == 1.0
    assert finished.result["findings_created"] == 4
    assert "4 finding(s)" in finished.message


def test_cancelled_result_marks_the_job_cancelled(db_session, case):
    from app.services.engine import InvestigationResult

    job = job_service.create_job(db_session, case.id)
    finished = job_service.finish_job(
        db_session, job.id, InvestigationResult(case_id=case.id, cancelled=True)
    )
    assert finished.state is JobState.CANCELLED


def test_failure_is_recorded_with_its_type(db_session, case):
    job = job_service.create_job(db_session, case.id)
    failed = job_service.fail_job(db_session, job.id, RuntimeError("collector exploded"))
    assert failed.state is JobState.FAILED
    assert failed.error_type == "RuntimeError"
    assert "collector exploded" in failed.error_message


def test_listing_jobs_filters_by_case(db_session, case):
    other = case_service.create_case(db_session, CaseCreate(name="Other"))
    db_session.flush()
    job_service.create_job(db_session, case.id)
    job_service.create_job(db_session, other.id)

    assert len(job_service.list_jobs(db_session, case.id)) == 1
    assert len(job_service.list_jobs(db_session)) == 2


def test_inline_dispatch_runs_the_investigation(db_session, case, monkeypatch, tmp_path):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)
    reset_settings_cache()
    try:
        job = job_service.create_job(db_session, case.id)
        outcome = job_service.dispatch_job(db_session, job)
        assert outcome["dispatched"] == "inline"
        assert job_service.get_job(db_session, job.id).state is JobState.COMPLETE
    finally:
        reset_settings_cache()


def test_dispatch_falls_back_inline_when_the_broker_is_unreachable(
    db_session, case, monkeypatch, tmp_path
):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)
    reset_settings_cache()

    class _Broken:
        @staticmethod
        def apply_async(*args, **kwargs):
            raise OSError("broker unreachable")

    monkeypatch.setattr("app.workers.tasks.run_investigation_task", _Broken)
    try:
        job = job_service.create_job(db_session, case.id)
        outcome = job_service.dispatch_job(db_session, job)
        assert outcome["dispatched"] == "inline"
        assert "unreachable" in outcome["reason"].lower()
        assert job_service.get_job(db_session, job.id).state is JobState.COMPLETE
    finally:
        reset_settings_cache()


def test_dispatch_uses_celery_when_available(db_session, case, monkeypatch):
    class _AsyncResult:
        id = "celery-task-123"

    class _Task:
        @staticmethod
        def apply_async(*args, **kwargs):
            return _AsyncResult()

    monkeypatch.setattr("app.workers.tasks.run_investigation_task", _Task)
    job = job_service.create_job(db_session, case.id)
    outcome = job_service.dispatch_job(db_session, job)
    assert outcome == {"dispatched": "celery", "celery_id": "celery-task-123"}
    assert job_service.get_job(db_session, job.id).celery_id == "celery-task-123"
