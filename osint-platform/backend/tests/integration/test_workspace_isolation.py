"""The adversarial suite: user B against user A's workspace.

Every one of these operations succeeded before this change, for anybody who knew
a UUID. That is what "UUID != authorization" means in practice, and this file is
the proof that it is no longer true.

The shape of the assertions matters as much as the fact of them. A cross-workspace
object answers **404**, not 403: a 403 would confirm that the id names a real
case, which is exactly what an attacker enumerating ids is trying to learn. 403
is for the object the caller *can* see and may not act on.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.db import get_session_factory
from app.models.enums import WorkspaceRole
from tests.conftest import TEST_PASSWORD, _bootstrap_account, _sign_in

pytestmark = pytest.mark.anyio

#: A well-formed UUID that names nothing. Used to show that "does not exist" and
#: "is not yours" are answered identically.
ABSENT = "00000000-0000-4000-8000-000000000000"


async def _client_for(app, email: str) -> httpx.AsyncClient:
    """A second signed-in client against the same application and database."""
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    await _sign_in(client, email)
    return client


@pytest.fixture
async def two_tenants(anonymous_client):
    """Alice owns a case. Mallory owns a different workspace and wants it.

    Returns ``(alice, mallory, case_id, alice_workspace, mallory_workspace)``.
    """
    app = anonymous_client._transport.app
    _, alice_workspace = _bootstrap_account(email="alice@example.com", workspace="Alice Ltd")
    _, mallory_workspace = _bootstrap_account(email="mallory@example.com", workspace="Mallory Ltd")

    alice = anonymous_client
    await _sign_in(alice, "alice@example.com")
    created = await alice.post("/api/v1/cases", json={"name": "Alice's investigation"})
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    target = await alice.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "example.com", "type": "DOMAIN"}
    )
    assert target.status_code in (200, 201), target.text

    mallory = await _client_for(app, "mallory@example.com")
    try:
        yield alice, mallory, case_id, alice_workspace, mallory_workspace
    finally:
        await mallory.aclose()


# --------------------------------------------------------------- the matrix


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/api/v1/cases/{case}", None),
        ("GET", "/api/v1/cases/{case}/summary", None),
        ("PATCH", "/api/v1/cases/{case}", {"name": "Renamed by Mallory"}),
        ("DELETE", "/api/v1/cases/{case}", None),
        ("POST", "/api/v1/cases/{case}/run", {}),
        ("GET", "/api/v1/cases/{case}/report", None),
        ("GET", "/api/v1/cases/{case}/findings", None),
        ("GET", "/api/v1/cases/{case}/evidence", None),
        ("GET", "/api/v1/cases/{case}/evidence/verify", None),
        ("GET", "/api/v1/cases/{case}/runs", None),
        ("GET", "/api/v1/cases/{case}/jobs", None),
        ("GET", "/api/v1/cases/{case}/entities", None),
        ("GET", "/api/v1/cases/{case}/relationships", None),
        ("GET", "/api/v1/cases/{case}/graph", None),
        ("GET", "/api/v1/cases/{case}/timeline", None),
        ("GET", "/api/v1/cases/{case}/targets", None),
        ("POST", "/api/v1/cases/{case}/targets", {"value": "evil.example", "type": "DOMAIN"}),
        ("GET", "/api/v1/cases/{case}/social-profiles", None),
        ("GET", "/api/v1/cases/{case}/public-contacts", None),
        ("GET", "/api/v1/cases/{case}/images", None),
        ("GET", "/api/v1/cases/{case}/candidates", None),
        ("GET", "/api/v1/cases/{case}/decisions", None),
        ("GET", "/api/v1/cases/{case}/recon-results", None),
    ],
)
async def test_another_workspace_is_not_reachable_by_uuid(two_tenants, method, path, body):
    """The whole case-scoped surface, one request each."""
    _alice, mallory, case_id, _aws, _mws = two_tenants
    response = await mallory.request(method, path.format(case=case_id), json=body)
    assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
    assert response.json()["code"] == "not_found"


async def test_a_foreign_case_is_indistinguishable_from_one_that_does_not_exist(two_tenants):
    """The property that makes id enumeration pointless."""
    _alice, mallory, case_id, _aws, _mws = two_tenants
    real = await mallory.get(f"/api/v1/cases/{case_id}")
    absent = await mallory.get(f"/api/v1/cases/{ABSENT}")
    assert real.status_code == absent.status_code == 404
    assert real.json()["code"] == absent.json()["code"]


async def test_a_foreign_case_never_appears_in_a_listing(two_tenants):
    _alice, mallory, case_id, _aws, _mws = two_tenants
    listing = await mallory.get("/api/v1/cases")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    assert case_id not in listing.text


async def test_a_foreign_job_is_not_reachable_by_its_own_id(two_tenants):
    """The route with no case in the path, and the most exposed one before this."""
    alice, mallory, case_id, _aws, _mws = two_tenants
    started = await alice.post(f"/api/v1/cases/{case_id}/run", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job"]["id"]

    assert (await mallory.get(f"/api/v1/jobs/{job_id}")).status_code == 404
    assert (await mallory.post(f"/api/v1/jobs/{job_id}/cancel")).status_code == 404
    # And it is absent from the cross-case listing, which used to disclose other
    # customers' case ids and run times without naming a case.
    listing = await mallory.get("/api/v1/jobs")
    assert listing.status_code == 200
    assert job_id not in listing.text


async def test_a_foreign_target_is_not_reachable(two_tenants):
    alice, mallory, case_id, _aws, _mws = two_tenants
    targets = (await alice.get(f"/api/v1/cases/{case_id}/targets")).json()["items"]
    target_id = targets[0]["id"]
    for method in ("GET", "PATCH", "DELETE"):
        response = await mallory.request(
            method, f"/api/v1/cases/{case_id}/targets/{target_id}", json={}
        )
        assert response.status_code == 404


async def test_a_foreign_analyst_decision_cannot_be_written(two_tenants):
    _alice, mallory, case_id, _aws, _mws = two_tenants
    response = await mallory.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={"subject_type": "CANDIDATE", "subject_id": ABSENT, "decision": "CONFIRMED"},
    )
    assert response.status_code == 404


async def test_a_foreign_import_is_refused(two_tenants):
    alice, mallory, case_id, _aws, _mws = two_tenants
    person = await alice.post(
        f"/api/v1/cases/{case_id}/targets",
        json={"value": "Example Person", "type": "PERSON"},
    )
    assert person.status_code in (200, 201)
    target_id = person.json()["id"]
    response = await mallory.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                {
                    "query": '"Example Person"',
                    "url": "https://example.org/planted",
                    "title": "Planted",
                    "engine": "Google",
                }
            ]
        },
    )
    assert response.status_code == 404


async def test_a_foreign_workspace_and_its_audit_log_are_not_reachable(two_tenants):
    _alice, mallory, _case, alice_workspace, _mws = two_tenants
    assert (await mallory.get(f"/api/v1/workspaces/{alice_workspace}")).status_code == 404
    assert (await mallory.get(f"/api/v1/workspaces/{alice_workspace}/members")).status_code == 404
    assert (await mallory.get(f"/api/v1/workspaces/{alice_workspace}/audit")).status_code == 404
    response = await mallory.post(
        f"/api/v1/workspaces/{alice_workspace}/members",
        json={"email": "mallory@example.com", "role": "OWNER"},
    )
    assert response.status_code == 404


async def test_a_case_cannot_be_created_into_a_foreign_workspace(two_tenants):
    """Naming somebody else's workspace on the payload must not place a case in it."""
    _alice, mallory, _case, alice_workspace, _mws = two_tenants
    response = await mallory.post(
        "/api/v1/cases", json={"name": "Planted", "workspace_id": alice_workspace}
    )
    assert response.status_code == 404


async def test_an_unclaimed_case_is_invisible_to_everyone(anonymous_client, db_session):
    """A pre-upgrade case belongs to nobody until an operator adopts it.

    Not "belongs to whoever asks first" — which is what any default assignment
    would have amounted to.
    """
    from app.models import Case

    _bootstrap_account()
    await _sign_in(anonymous_client)
    case = Case(name="Legacy, unclaimed")
    db_session.add(case)
    db_session.commit()

    assert (await anonymous_client.get(f"/api/v1/cases/{case.id}")).status_code == 404
    listing = await anonymous_client.get("/api/v1/cases")
    assert str(case.id) not in listing.text


# ------------------------------------------------------------- role matrix


@pytest.fixture
async def viewer(anonymous_client):
    """A VIEWER in a workspace that already has a case."""
    app = anonymous_client._transport.app
    _bootstrap_account(email="owner@example.com", workspace="Shared")
    owner = anonymous_client
    await _sign_in(owner, "owner@example.com")
    created = await owner.post("/api/v1/cases", json={"name": "Shared case"})
    case_id = created.json()["id"]

    from app.models.auth import Workspace
    from app.services import accounts

    with get_session_factory()() as session:
        workspace = session.query(Workspace).filter(Workspace.slug == "shared").one()
        user = accounts.create_user(session, email="viewer@example.com", password=TEST_PASSWORD)
        accounts.add_member(session, workspace=workspace, user=user, role=WorkspaceRole.VIEWER)
        session.commit()

    client = await _client_for(app, "viewer@example.com")
    try:
        yield owner, client, case_id
    finally:
        await client.aclose()


async def test_a_viewer_reads_but_changes_nothing(viewer):
    """Every refusal is a 403, not a 404: a VIEWER may see the case."""
    _owner, reader, case_id = viewer

    assert (await reader.get(f"/api/v1/cases/{case_id}")).status_code == 200
    assert (await reader.get(f"/api/v1/cases/{case_id}/findings")).status_code == 200
    assert (await reader.get(f"/api/v1/cases/{case_id}/report")).status_code == 200

    refusals = [
        ("POST", f"/api/v1/cases/{case_id}/run", {}),
        ("DELETE", f"/api/v1/cases/{case_id}", None),
        ("PATCH", f"/api/v1/cases/{case_id}", {"name": "Renamed"}),
        ("POST", f"/api/v1/cases/{case_id}/targets", {"value": "example.net", "type": "DOMAIN"}),
        (
            "POST",
            f"/api/v1/cases/{case_id}/decisions",
            {"subject_type": "CANDIDATE", "subject_id": ABSENT, "decision": "CONFIRMED"},
        ),
        ("POST", "/api/v1/cases", {"name": "A viewer's case"}),
    ]
    for method, path, body in refusals:
        response = await reader.request(method, path, json=body)
        assert response.status_code in (403, 404), f"{method} {path} -> {response.status_code}"
        if response.status_code == 403:
            assert response.json()["code"] == "permission_denied"


async def test_a_viewer_cannot_manage_membership(viewer):
    _owner, reader, _case = viewer
    workspaces = (await reader.get("/api/v1/workspaces")).json()
    workspace_id = workspaces[0]["workspace"]["id"]
    response = await reader.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": "owner@example.com", "role": "ADMIN"},
    )
    assert response.status_code == 403


async def test_an_analyst_cannot_manage_membership_or_delete_a_case(anonymous_client):
    """The ANALYST ceiling: investigation work, no authority over people."""
    app = anonymous_client._transport.app
    _bootstrap_account(email="boss@example.com", workspace="Firm")
    boss = anonymous_client
    await _sign_in(boss, "boss@example.com")
    case_id = (await boss.post("/api/v1/cases", json={"name": "Firm case"})).json()["id"]

    from app.models.auth import Workspace
    from app.services import accounts

    with get_session_factory()() as session:
        workspace = session.query(Workspace).filter(Workspace.slug == "firm").one()
        user = accounts.create_user(session, email="an@example.com", password=TEST_PASSWORD)
        accounts.add_member(session, workspace=workspace, user=user, role=WorkspaceRole.ANALYST)
        session.commit()
        workspace_id = str(workspace.id)

    analyst = await _client_for(app, "an@example.com")
    try:
        # Allowed: the work.
        assert (await analyst.post(f"/api/v1/cases/{case_id}/run", json={})).status_code == 202
        # Refused: the authority.
        assert (await analyst.delete(f"/api/v1/cases/{case_id}")).status_code == 403
        members = await analyst.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            json={"email": "boss@example.com", "role": "VIEWER"},
        )
        assert members.status_code == 403
        assert (await analyst.get(f"/api/v1/workspaces/{workspace_id}/audit")).status_code == 403
    finally:
        await analyst.aclose()


async def test_an_admin_cannot_transfer_ownership_or_mint_an_owner(anonymous_client):
    """The one permission that would collapse ADMIN and OWNER into one role."""
    app = anonymous_client._transport.app
    _bootstrap_account(email="theowner@example.com", workspace="Firm Two")
    owner = anonymous_client
    await _sign_in(owner, "theowner@example.com")

    from app.models.auth import Workspace
    from app.services import accounts

    with get_session_factory()() as session:
        workspace = session.query(Workspace).filter(Workspace.slug == "firm-two").one()
        user = accounts.create_user(session, email="admin@example.com", password=TEST_PASSWORD)
        membership = accounts.add_member(
            session, workspace=workspace, user=user, role=WorkspaceRole.ADMIN
        )
        session.commit()
        workspace_id = str(workspace.id)
        membership_id = str(membership.id)

    admin = await _client_for(app, "admin@example.com")
    try:
        # Cannot promote themselves to OWNER through the role endpoint.
        promote = await admin.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{membership_id}",
            json={"role": "OWNER"},
        )
        assert promote.status_code == 403
        # Cannot use the transfer endpoint either.
        transfer = await admin.post(
            f"/api/v1/workspaces/{workspace_id}/transfer-ownership",
            params={"membership_id": membership_id},
            json={"role": "OWNER"},
        )
        assert transfer.status_code == 403
        # But may do ordinary admin work.
        assert (await admin.get(f"/api/v1/workspaces/{workspace_id}/audit")).status_code == 200
    finally:
        await admin.aclose()


async def test_the_last_owner_cannot_be_removed_or_demoted(api_client):
    """A workspace with no owner is a support ticket that cannot be closed."""
    workspaces = (await api_client.get("/api/v1/workspaces")).json()
    workspace_id = workspaces[0]["workspace"]["id"]
    members = (await api_client.get(f"/api/v1/workspaces/{workspace_id}/members")).json()
    owner = next(item for item in members if item["role"] == "OWNER")

    demote = await api_client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner['id']}", json={"role": "ADMIN"}
    )
    assert demote.status_code == 409
    remove = await api_client.delete(f"/api/v1/workspaces/{workspace_id}/members/{owner['id']}")
    assert remove.status_code == 409


async def test_removing_a_member_ends_their_sessions_immediately(anonymous_client):
    """Not "whenever their session happens to expire"."""
    app = anonymous_client._transport.app
    _bootstrap_account(email="chief@example.com", workspace="Firm Three")
    chief = anonymous_client
    await _sign_in(chief, "chief@example.com")

    from app.models.auth import Workspace
    from app.services import accounts

    with get_session_factory()() as session:
        workspace = session.query(Workspace).filter(Workspace.slug == "firm-three").one()
        user = accounts.create_user(session, email="leaver@example.com", password=TEST_PASSWORD)
        membership = accounts.add_member(
            session, workspace=workspace, user=user, role=WorkspaceRole.ANALYST
        )
        session.commit()
        workspace_id = str(workspace.id)
        membership_id = str(membership.id)

    leaver = await _client_for(app, "leaver@example.com")
    try:
        assert (await leaver.get("/api/v1/cases")).status_code == 200
        removed = await chief.delete(f"/api/v1/workspaces/{workspace_id}/members/{membership_id}")
        assert removed.status_code == 204
        assert (await leaver.get("/api/v1/cases")).status_code == 401
    finally:
        await leaver.aclose()
