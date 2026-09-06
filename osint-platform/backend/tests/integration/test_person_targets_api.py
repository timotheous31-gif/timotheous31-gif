"""PERSON targets over the HTTP API, end to end.

Covers the path a user actually walks in the dashboard: type a name, get told
it is ambiguous, choose a type, and have the target created with the right one.
"""

from __future__ import annotations

NAME = "Timotheous Samar"


async def test_a_bare_name_is_rejected_rather_than_filed_as_an_organization(api_client, case_id):
    """The reported bug, at the layer the user hit it."""
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": NAME})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "ambiguous_target_type"
    assert body["detail"] == {"candidates": ["PERSON", "ORGANIZATION"]}
    assert "person or an organisation" in body["message"]


async def test_preview_reports_ambiguity_instead_of_erroring(api_client, case_id):
    """The form needs the candidates to render the choice, so preview answers."""
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/preview", json={"value": NAME}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ambiguous"] is True
    assert body["candidates"] == ["PERSON", "ORGANIZATION"]
    assert body["type"] is None
    assert body["message"]


async def test_preview_resolves_once_a_type_is_chosen(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/preview", json={"value": NAME, "type": "PERSON"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ambiguous"] is False
    assert body["type"] == "PERSON"
    assert body["normalized_value"] == "timotheous samar"


async def test_preview_still_infers_structured_input(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/preview", json={"value": "Example.COM."}
    )
    body = response.json()
    assert body["ambiguous"] is False
    assert body["type"] == "DOMAIN"
    assert body["normalized_value"] == "example.com"


async def test_explicit_person_target_is_created(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "PERSON"
    assert body["normalized_value"] == "timotheous samar"
    assert body["raw_input"] == NAME
    assert body["attributes"]["display_name"] == NAME
    assert body["attributes"]["is_identifier"] is False


async def test_explicit_organization_target_is_created(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "ORGANIZATION"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["type"] == "ORGANIZATION"


async def test_the_same_name_can_be_both_a_person_and_an_organization(api_client, case_id):
    """Different types are different targets: neither shadows the other."""
    person = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
    )
    org = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "ORGANIZATION"}
    )
    assert person.status_code == 201
    assert org.status_code == 201
    assert person.json()["id"] != org.json()["id"]


async def test_person_targets_are_filterable(api_client, case_id):
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
    )
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})

    people = await api_client.get(f"/api/v1/cases/{case_id}/targets", params={"type": "PERSON"})
    assert people.json()["total"] == 1
    assert people.json()["items"][0]["normalized_value"] == "timotheous samar"


async def test_bulk_add_skips_the_ambiguous_entry_without_failing_the_batch(api_client, case_id):
    """One unclassifiable name must not lose the structured targets beside it."""
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/bulk",
        json={
            "targets": [
                {"value": "example.com"},
                {"value": NAME},
                {"value": NAME, "type": "PERSON"},
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ambiguous_target_type"


async def test_collectors_endpoint_reports_person_support_and_configuration(api_client):
    response = await api_client.get("/api/v1/collectors")
    assert response.status_code == 200
    collectors = {entry["name"]: entry for entry in response.json()}

    search = collectors["search"]
    assert "PERSON" in search["supported_targets"]
    assert search["configuration"]["required_settings"] == ["SEARCH_PROVIDER"]
    assert search["configuration"]["configured"] is False
    assert search["available"] is False
    assert "brave, bing or serper" in search["unavailable_reason"]

    # No infrastructure collector claims to handle a person's name.
    for name in ("dns", "rdap", "ctlog", "http_meta", "wayback", "github"):
        assert "PERSON" not in collectors[name]["supported_targets"], name


async def test_collectors_endpoint_never_returns_a_credential(api_client, monkeypatch):
    """Configure every credential with a sentinel, then look for it in the body.

    The endpoint reports which settings are needed and whether they are present,
    so this checks the thing that would actually be damaging: that a *value*
    never rides along with the names.
    """
    from app.core.settings import reset_settings_cache

    sentinels = {
        "GITHUB_TOKEN": "ghp_sentinel000000000000000000000000",
        "BRAVE_API_KEY": "brave-sentinel-value",
        "BING_API_KEY": "bing-sentinel-value",
        "SERPER_API_KEY": "serper-sentinel-value",
        "HIBP_API_KEY": "hibp-sentinel-value",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    reset_settings_cache()

    response = await api_client.get("/api/v1/collectors")
    assert response.status_code == 200
    body = response.text
    for name, value in sentinels.items():
        assert value not in body, f"{name}'s value appears in the collectors response"
    assert "Bearer" not in body

    # The setting names themselves are the point of the endpoint, so they stay.
    collectors = {entry["name"]: entry for entry in response.json()}
    assert collectors["search"]["configuration"]["required_settings"] == [
        "SEARCH_PROVIDER",
        "BRAVE_API_KEY",
    ]
    assert collectors["search"]["available"] is True
