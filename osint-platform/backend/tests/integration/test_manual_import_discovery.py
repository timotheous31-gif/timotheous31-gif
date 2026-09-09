"""Manual import, and the promise that pasting a URL buys no confidence.

The whole zero-cost workflow rests on one rule: an investigator choosing a
result is a judgement about relevance, not evidence about identity. So an
imported URL runs through exactly the correlation a collected one does, and a
handle typed into a form is recorded as the investigator's reading rather than
as the page's own claim.
"""

from __future__ import annotations

import pytest

NAME = "Example Person"
LINKEDIN = "https://www.linkedin.com/in/example-person"


async def _target(api_client, case_id, context: dict | None = None):
    body: dict = {"value": NAME, "type": "PERSON"}
    if context:
        body["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _import(api_client, case_id, target_id, **fields):
    payload = {
        "query": '"Example Person" site:linkedin.com/in',
        "url": LINKEDIN,
        "title": "Example Person — LinkedIn",
        "snippet": "Lecturer at Example Institute",
        "engine": "Google",
        **fields,
    }
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [payload]},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _profiles(api_client, case_id):
    return (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()


async def test_an_import_with_no_anchor_stays_at_the_name_only_floor(api_client, case_id):
    target_id = await _target(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await _profiles(api_client, case_id))[0]
    assert profile["platform"] == "linkedin"
    assert profile["confidence"] <= 0.30, "choosing a result is not evidence of identity"
    assert profile["corroborated_by"] == []
    assert profile["discovery_method"] == "manual_import"
    assert profile["evidence_class"] == "investigator_imported"


async def test_an_import_uses_the_same_anchor_correlation_as_a_collector(api_client, case_id):
    target_id = await _target(api_client, case_id, {"known_usernames": ["example-person"]})
    await _import(api_client, case_id, target_id)
    profile = (await _profiles(api_client, case_id))[0]
    assert "username" in profile["corroborated_by"]
    assert profile["confidence"] > 0.30


async def test_a_pasted_url_gets_no_shortcut_over_the_same_url_collected(api_client, case_id):
    """No import path may award confidence a collector would not."""
    from app.collectors.person import PersonContext
    from app.collectors.social import classify_url
    from app.services.social_profiles import assess_profile

    target_id = await _target(api_client, case_id, {"known_usernames": ["example-person"]})
    await _import(api_client, case_id, target_id)
    profile = (await _profiles(api_client, case_id))[0]

    classified = classify_url(LINKEDIN)
    expected, _match, _mismatch, _corroborated = assess_profile(
        classified,
        subject_name=NAME,
        context=PersonContext(known_usernames=("example-person",)),
    )
    assert profile["confidence"] == pytest.approx(expected, abs=1e-9)


async def test_a_typed_handle_is_recorded_as_the_investigators_reading(api_client, case_id):
    target_id = await _target(api_client, case_id)
    # A URL whose shape yields no handle, so the typed one is the only source.
    imported = await _import(
        api_client,
        case_id,
        target_id,
        url="https://example.org/staff/directory",
        handle="example-person",
        display_name="Example Person Dass",
    )
    assert imported[0]["handle"] == "example-person"

    findings = (await api_client.get(f"/api/v1/cases/{case_id}/findings")).json()["items"]
    match = next(item for item in findings if "staff/directory" in str(item["data"].get("url")))
    assert match["data"]["handle_source"] == "investigator"
    assert match["data"]["displayed_name"] == "Example Person Dass"


async def test_the_url_shape_beats_a_typed_handle_when_both_exist(api_client, case_id):
    target_id = await _target(api_client, case_id)
    await _import(api_client, case_id, target_id, handle="somebody-else")
    findings = (await api_client.get(f"/api/v1/cases/{case_id}/findings")).json()["items"]
    match = next(item for item in findings if item["data"].get("url") == LINKEDIN)
    assert match["data"]["handle"] == "example-person"
    assert match["data"]["handle_source"] == "url_shape"


async def test_a_displayed_name_never_becomes_the_targets_name(api_client, case_id):
    target_id = await _target(api_client, case_id)
    await _import(api_client, case_id, target_id, display_name="Someone Else Entirely")
    target = (await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}")).json()
    assert target["normalized_value"] == NAME.lower()
    assert target["attributes"]["display_name"] == NAME


async def test_an_unsafe_import_url_is_refused(api_client, case_id):
    target_id = await _target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                {
                    "query": "test",
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "engine": "Google",
                }
            ]
        },
    )
    # A ValidationError from the SSRF guard, not a schema rejection: the URL is
    # well-formed and simply not one this platform will touch.
    assert response.status_code == 400, response.text
    assert not await _profiles(api_client, case_id)
    assert not (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()


async def test_an_imported_image_is_reference_only_until_someone_fetches_it(api_client, case_id):
    target_id = await _target(api_client, case_id)
    await _import(
        api_client,
        case_id,
        target_id,
        image_url="https://example.com/portrait.jpg",
        caption="Staff photograph",
    )
    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert len(images) == 1
    assert images[0]["fetch_state"] == "REFERENCE_ONLY"
    assert images[0]["sha256"] is None
    assert images[0]["attributes"]["biometric_matching"] is False


async def test_the_whole_discovery_workflow_runs_with_no_search_provider(api_client, case_id):
    """$0 mode is the default, and it reaches every step of the workflow."""
    from app.core.settings import get_settings

    assert str(get_settings().search_provider) in {"none", "SearchProvider.NONE"}

    target_id = await _target(
        api_client, case_id, {"github_username": "example-person", "country": "Example Republic"}
    )
    plan = (
        await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    ).json()
    assert plan["queries"], "queries must be generated with no provider configured"
    assert plan["capabilities"], "the plan must explain what can and cannot be checked"
    assert "does not submit" in plan["execution"]

    families = {query["family"] for query in plan["queries"]}
    assert {"social", "image", "handle"} <= families

    await _import(api_client, case_id, target_id)
    assert await _profiles(api_client, case_id)

    report = (await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})).text
    assert "## Public profiles and contacts" in report
    assert "LinkedIn" in report


async def test_the_plan_states_that_blocked_platforms_are_not_queried(api_client, case_id):
    target_id = await _target(api_client, case_id)
    plan = (
        await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    ).json()
    linkedin = next(item for item in plan["capabilities"] if item["platform"] == "linkedin")
    assert linkedin["handle_check_supported"] is False
    assert linkedin["manual_search_supported"] is True
    assert linkedin["notes"]
