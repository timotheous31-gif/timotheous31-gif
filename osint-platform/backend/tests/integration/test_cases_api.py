"""Case and target CRUD through the HTTP API."""

from __future__ import annotations

import pytest


async def test_create_and_fetch_case(api_client):
    response = await api_client.post(
        "/api/v1/cases",
        json={
            "name": "Example Domain Investigation",
            "description": "Due-diligence review of a fictional domain",
            "tags": ["Due-Diligence", "demo", "demo"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Example Domain Investigation"
    assert body["status"] == "NEW"
    assert sorted(tag["name"] for tag in body["tags"]) == ["demo", "due-diligence"]

    fetched = await api_client.get(f"/api/v1/cases/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


async def test_case_name_is_required(api_client):
    response = await api_client.post("/api/v1/cases", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_missing_case_returns_not_found(api_client):
    response = await api_client.get("/api/v1/cases/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_malformed_case_id_returns_422(api_client):
    response = await api_client.get("/api/v1/cases/not-a-uuid")
    assert response.status_code == 422


async def test_list_cases_paginates_and_filters(api_client):
    for index in range(3):
        await api_client.post("/api/v1/cases", json={"name": f"Case {index}", "tags": ["batch"]})
    await api_client.post("/api/v1/cases", json={"name": "Unrelated"})

    listing = await api_client.get("/api/v1/cases", params={"limit": 2})
    body = listing.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2

    tagged = await api_client.get("/api/v1/cases", params={"tag": "batch"})
    assert tagged.json()["total"] == 3

    searched = await api_client.get("/api/v1/cases", params={"q": "Unrelated"})
    assert searched.json()["total"] == 1


async def test_update_case_status_and_tags(api_client, case_id):
    response = await api_client.patch(
        f"/api/v1/cases/{case_id}", json={"status": "RUNNING", "tags": ["active"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUNNING"
    assert [tag["name"] for tag in body["tags"]] == ["active"]


async def test_delete_case_removes_it(api_client, case_id):
    assert (await api_client.delete(f"/api/v1/cases/{case_id}")).status_code == 204
    assert (await api_client.get(f"/api/v1/cases/{case_id}")).status_code == 404


async def test_add_target_infers_type(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "  Example.COM. "}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "DOMAIN"
    assert body["normalized_value"] == "example.com"
    assert body["raw_input"] == "Example.COM."
    assert body["status"] == "PENDING"


async def test_duplicate_target_conflicts(api_client, case_id):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    duplicate = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "EXAMPLE.com"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"


async def test_invalid_target_is_rejected(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "not a domain", "type": "DOMAIN"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_bulk_add_skips_duplicates(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/bulk",
        json={
            "targets": [
                {"value": "example.com"},
                {"value": "example.com"},
                {"value": "example.org"},
                {"value": "@exampleuser"},
            ]
        },
    )
    assert response.status_code == 201
    values = sorted(item["normalized_value"] for item in response.json())
    assert values == ["example.com", "example.org", "exampleuser"]


async def test_list_and_filter_targets(api_client, case_id):
    for value in ("example.com", "example.org", "@exampleuser"):
        await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": value})

    listing = await api_client.get(f"/api/v1/cases/{case_id}/targets")
    assert listing.json()["total"] == 3

    domains = await api_client.get(f"/api/v1/cases/{case_id}/targets", params={"type": "DOMAIN"})
    assert domains.json()["total"] == 2


async def test_update_and_delete_target(api_client, case_id):
    created = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"}
    )
    target_id = created.json()["id"]

    updated = await api_client.patch(
        f"/api/v1/cases/{case_id}/targets/{target_id}",
        json={"notes": "Registrar lookup pending", "status": "SKIPPED"},
    )
    assert updated.json()["notes"] == "Registrar lookup pending"
    assert updated.json()["status"] == "SKIPPED"

    assert (
        await api_client.delete(f"/api/v1/cases/{case_id}/targets/{target_id}")
    ).status_code == 204
    assert (await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}")).status_code == 404


async def test_normalization_preview_does_not_persist(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/preview", json={"value": "@ExampleUser"}
    )
    assert response.status_code == 200
    assert response.json()["normalized_value"] == "exampleuser"
    listing = await api_client.get(f"/api/v1/cases/{case_id}/targets")
    assert listing.json()["total"] == 0


async def test_case_summary_counts(api_client, case_id):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    response = await api_client.get(f"/api/v1/cases/{case_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["targets"] == 1
    assert body["findings"] == 0
    assert body["collectors_run"] == []
    assert body["confidence_distribution"] == {"high": 0, "medium": 0, "low": 0}


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "example.com", "type": "IP"},
        {"value": "@bad user"},
    ],
)
async def test_target_validation_errors(api_client, case_id, payload):
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=payload)
    assert response.status_code == 422
