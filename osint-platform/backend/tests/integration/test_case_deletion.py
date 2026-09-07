"""Deleting a case removes everything it owns, and nothing it merely borrows.

Two properties matter and pull against each other: no orphaned rows, and no
collateral damage to records the case shares with others. Tags are the sharp
edge — several cases can carry the same tag, so the link goes and the tag stays.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models import (
    Case,
    CollectorRun,
    Entity,
    Evidence,
    Finding,
    Job,
    Relationship,
    Tag,
    Target,
    TimelineEvent,
)
from app.models.enums import CaseStatus, JobState

CASE_SCOPED = (Target, Finding, Entity, Relationship, Evidence, TimelineEvent, CollectorRun, Job)


def session():
    """A session on the database ``api_client`` is using.

    Deliberately not the ``db_session`` fixture: both it and ``api_client`` call
    ``configure_engine`` on a fresh in-memory SQLite database, so requesting
    both in one test rebinds the global engine and the second one silently
    discards the first one's data.
    """
    return get_session_factory()()


async def _case_with_data(api_client, name="Deletable"):
    """A case carrying at least one row in every case-scoped table."""
    response = await api_client.post("/api/v1/cases", json={"name": name, "tags": ["shared-tag"]})
    assert response.status_code == 201, response.text
    case_id = response.json()["id"]
    target = await api_client.post(
        f"/api/v1/cases/{case_id}/targets",
        json={
            "value": "Example Person",
            "type": "PERSON",
            "context": {"github_username": "octocat"},
        },
    )
    assert target.status_code == 201, target.text
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target.json()['id']}/recon-results",
        json={
            "results": [
                {
                    "query": '"Example Person"',
                    "url": "https://example.org/profile",
                    "title": "Example",
                    "engine": "Google",
                }
            ]
        },
    )
    return case_id


def _counts(db, case_id):
    return {
        model.__name__: db.scalar(
            select(func.count()).select_from(model).where(model.case_id == case_id)
        )
        for model in CASE_SCOPED
    }


# ------------------------------------------------------------------ happy paths


async def test_deleting_an_unrun_case_returns_204(api_client):
    case_id = (await api_client.post("/api/v1/cases", json={"name": "Unrun"})).json()["id"]
    response = await api_client.delete(f"/api/v1/cases/{case_id}")
    assert response.status_code == 204
    assert (await api_client.get(f"/api/v1/cases/{case_id}")).status_code == 404


@pytest.mark.parametrize(
    "state", [CaseStatus.NEW, CaseStatus.COMPLETE, CaseStatus.PAUSED, CaseStatus.ARCHIVED]
)
async def test_a_case_can_be_deleted_in_any_status(api_client, state):
    case_id = (await api_client.post("/api/v1/cases", json={"name": f"Case {state}"})).json()["id"]
    patched = await api_client.patch(f"/api/v1/cases/{case_id}", json={"status": str(state)})
    assert patched.status_code == 200, patched.text
    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204


async def test_deleting_an_unknown_case_returns_404(api_client):
    response = await api_client.delete(f"/api/v1/cases/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_a_case_with_data_leaves_no_orphaned_rows(api_client):
    case_id = await _case_with_data(api_client)
    with session() as db:
        before = _counts(db, uuid.UUID(case_id))
    assert before["Target"] >= 1 and before["Finding"] >= 1, before
    assert before["Evidence"] >= 1, before

    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204

    with session() as db:
        after = _counts(db, uuid.UUID(case_id))
    assert all(count == 0 for count in after.values()), after


async def test_deletion_does_not_remove_tags_other_cases_share(api_client):
    first = await _case_with_data(api_client, name="First")
    second = await _case_with_data(api_client, name="Second")

    assert (await api_client.delete(f"/api/v1/cases/{first}")).status_code == 204

    # The tag is shared, so it must survive; the surviving case must keep it.
    with session() as db:
        assert db.scalar(select(func.count()).select_from(Tag).where(Tag.name == "shared-tag"))
    remaining = await api_client.get(f"/api/v1/cases/{second}")
    assert "shared-tag" in [tag["name"] for tag in remaining.json()["tags"]]


async def test_deleting_one_case_leaves_another_untouched(api_client):
    keep = await _case_with_data(api_client, name="Keep")
    drop = await _case_with_data(api_client, name="Drop")
    with session() as db:
        before = _counts(db, uuid.UUID(keep))

    assert (await api_client.delete(f"/api/v1/cases/{drop}")).status_code == 204

    with session() as db:
        assert _counts(db, uuid.UUID(keep)) == before
        assert db.get(Case, uuid.UUID(keep)) is not None


# --------------------------------------------------------------- active jobs


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING])
async def test_an_active_job_blocks_deletion_with_409(api_client, state):
    case_id = await _case_with_data(api_client, name="Busy")
    with session() as db:
        db.add(Job(case_id=uuid.UUID(case_id), state=state))
        db.commit()

    response = await api_client.delete(f"/api/v1/cases/{case_id}")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "conflict"
    # The message has to tell the operator what to do about it.
    assert "cancel" in body["message"].lower()
    assert str(state) in body["detail"]["states"]
    # And nothing may have been deleted.
    assert (await api_client.get(f"/api/v1/cases/{case_id}")).status_code == 200


@pytest.mark.parametrize("state", [JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED])
async def test_a_finished_job_does_not_block_deletion(api_client, state):
    case_id = await _case_with_data(api_client, name=f"Done {state}")
    with session() as db:
        db.add(Job(case_id=uuid.UUID(case_id), state=state))
        db.commit()
    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204


async def test_cancelling_then_deleting_succeeds(api_client):
    """The documented flow: cancel, observe it stop, then delete."""
    case_id = await _case_with_data(api_client, name="Cancel then delete")
    with session() as db:
        db.add(Job(case_id=uuid.UUID(case_id), state=JobState.RUNNING))
        db.commit()

    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 409
    with session() as db:
        job = db.scalars(select(Job).where(Job.case_id == uuid.UUID(case_id))).one()
        job.state = JobState.CANCELLED
        db.commit()
    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204


# ------------------------------------------------------- evidence files on disk


def test_evidence_files_are_removed_with_the_case(tmp_path):
    """Rows are not the whole story: the store writes bytes to disk."""
    from app.services.cases import _remove_evidence_files

    case_id = uuid.uuid4()
    other = uuid.uuid4()
    for owner in (case_id, other):
        directory = tmp_path / str(owner) / "ab"
        directory.mkdir(parents=True)
        (directory / "abc123.json").write_text("{}", encoding="utf-8")

    removed = _remove_evidence_files(case_id, tmp_path)

    assert removed == 1
    assert not (tmp_path / str(case_id)).exists()
    # Another case's evidence is a separate subtree and must be untouched.
    assert (tmp_path / str(other) / "ab" / "abc123.json").exists()


def test_removing_evidence_for_a_case_with_no_files_is_not_an_error(tmp_path):
    from app.services.cases import _remove_evidence_files

    assert _remove_evidence_files(uuid.uuid4(), tmp_path) == 0


def test_an_unwritable_evidence_directory_does_not_fail_the_deletion(tmp_path, monkeypatch):
    """The rows are already gone; leftover bytes are a cleanup problem, not a 500."""
    import shutil as shutil_module

    from app.services import cases as cases_service

    case_id = uuid.uuid4()
    (tmp_path / str(case_id)).mkdir(parents=True)
    (tmp_path / str(case_id) / "x.json").write_text("{}", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(shutil_module, "rmtree", boom)
    assert cases_service._remove_evidence_files(case_id, tmp_path) == 0
