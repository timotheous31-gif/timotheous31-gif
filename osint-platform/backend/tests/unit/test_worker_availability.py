"""A reachable broker is not a working system.

The failure being pinned: Redis healthy, celery-worker exited. ``apply_async``
succeeds, the task lands in a queue nobody consumes, and the job sits at 0%
QUEUED forever. Nothing throws, so the existing broker-unreachable fallback
cannot see it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import JobState
from app.services import jobs as job_service
from app.services.jobs import PROCESSING_UNAVAILABLE, WorkerStatus, effective_state


class _Inspect:
    def __init__(self, replies):
        self._replies = replies

    def ping(self):
        return self._replies


def _patch_control(monkeypatch, replies=None, raises=None):
    """Replace ``control`` on the real app object.

    Patching the module attribute is not enough: ``worker_status`` imports
    ``celery_app`` inside the function, and the object it binds is the one whose
    ``control`` actually talks to the broker.
    """
    from app.workers.celery_app import celery_app

    class _Control:
        def inspect(self, timeout=None, connection=None):
            if raises is not None:
                raise raises
            return _Inspect(replies)

    class _Connection:
        """A broker that connects instantly, so these tests exercise the reply."""

        def ensure_connection(self, **kwargs):
            return self

        def release(self):
            pass

    monkeypatch.setattr(celery_app, "control", _Control())
    monkeypatch.setattr(celery_app, "connection", lambda **kwargs: _Connection())


@pytest.fixture(autouse=True)
def _not_eager(monkeypatch):
    """Eager mode short-circuits the probe, so turn it off for these tests."""
    monkeypatch.setattr(
        job_service, "get_settings", lambda: SimpleNamespace(celery_task_always_eager=False)
    )


def _job(state=JobState.QUEUED):
    return SimpleNamespace(id=uuid.uuid4(), state=state)


# ------------------------------------------------------------- worker_status


def test_a_live_worker_is_reported_available(monkeypatch):
    _patch_control(monkeypatch, replies={"celery@host": {"ok": "pong"}})
    status = job_service.worker_status()
    assert status.available
    assert status.workers == ("celery@host",)


def test_no_reply_means_unavailable_and_says_so(monkeypatch):
    """The exact production case: broker up, nobody consuming."""
    _patch_control(monkeypatch, replies=None)
    status = job_service.worker_status()
    assert not status.available
    assert "no worker" in status.reason.lower()


def test_an_empty_reply_dict_is_also_unavailable(monkeypatch):
    _patch_control(monkeypatch, replies={})
    assert not job_service.worker_status().available


def test_an_unreachable_broker_is_unavailable_rather_than_an_exception(monkeypatch):
    _patch_control(monkeypatch, raises=OSError("connection refused"))
    status = job_service.worker_status()
    assert not status.available
    assert "broker unreachable" in status.reason
    assert "OSError" in status.reason


# ----------------------------------------------------------- effective_state


def test_a_queued_job_with_no_worker_reads_as_processing_unavailable():
    assert effective_state(_job(), WorkerStatus(False, (), "no worker")) == PROCESSING_UNAVAILABLE


def test_a_queued_job_with_a_worker_stays_queued():
    assert effective_state(_job(), WorkerStatus(True, ("celery@host",))) == str(JobState.QUEUED)


@pytest.mark.parametrize(
    "state", [JobState.RUNNING, JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED]
)
def test_a_job_that_is_not_queued_is_never_relabelled(state):
    """Worker availability says nothing about a job that is already past queueing."""
    assert effective_state(_job(state), WorkerStatus(False, (), "no worker")) == str(state)


def test_the_stored_state_is_never_mutated_by_reporting():
    job = _job()
    effective_state(job, WorkerStatus(False, (), "no worker"))
    assert job.state is JobState.QUEUED, "recovery depends on the row staying QUEUED"


def test_a_queued_job_recovers_when_the_worker_comes_back():
    """The row is a standing instruction; a returning worker must still run it."""
    job = _job()
    assert effective_state(job, WorkerStatus(False, (), "down")) == PROCESSING_UNAVAILABLE
    assert effective_state(job, WorkerStatus(True, ("celery@host",))) == str(JobState.QUEUED)


def test_eager_mode_reports_available_without_touching_the_broker(monkeypatch):
    monkeypatch.setattr(
        job_service, "get_settings", lambda: SimpleNamespace(celery_task_always_eager=True)
    )

    def explode(*args, **kwargs):
        raise AssertionError("eager mode must not probe the broker")

    _patch_control(monkeypatch, raises=AssertionError("must not be called"))
    assert job_service.worker_status().available


def test_the_probe_fails_fast_against_a_refused_broker(monkeypatch):
    """The probe sits on the API request path, so it must not retry.

    Celery is configured with broker_connection_retry_on_startup, and
    ``inspect(timeout=...)`` bounds only the wait for a *reply* — so against a
    refused broker the call spends seconds in connection backoff first. Measured
    at 6.1s before ``ensure_connection(max_retries=0)`` was added. A degraded
    broker must not become a slow API.
    """
    import time

    from app.workers.celery_app import celery_app

    attempts = []

    class _Connection:
        def ensure_connection(self, **kwargs):
            attempts.append(kwargs)
            raise OSError("Connection refused")

        def release(self):
            pass

    monkeypatch.setattr(celery_app, "connection", lambda **kwargs: _Connection())

    started = time.monotonic()
    status = job_service.worker_status(timeout=0.5)
    elapsed = time.monotonic() - started

    assert not status.available
    assert elapsed < 1.0, f"probe took {elapsed:.2f}s; it must not retry"
    # The guarantee is structural, not a timing coincidence: retries are off.
    assert attempts and attempts[0]["max_retries"] == 0


def test_the_connection_is_released_even_when_the_probe_fails(monkeypatch):
    from app.workers.celery_app import celery_app

    released = []

    class _Connection:
        def ensure_connection(self, **kwargs):
            raise OSError("Connection refused")

        def release(self):
            released.append(True)

    monkeypatch.setattr(celery_app, "connection", lambda **kwargs: _Connection())
    assert not job_service.worker_status().available
    assert released, "a probe must not leak a broker connection"
