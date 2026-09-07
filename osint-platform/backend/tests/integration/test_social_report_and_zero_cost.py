"""Report serialisation of the new evidence, and the $0 guarantee.

Two separate promises. The report must be able to carry social profiles, images,
provenance and analyst decisions — V3.1 builds the professional renderer on top
of this shape, so the shape has to be right now. And the whole workflow must
still run with no paid provider configured.
"""

from __future__ import annotations

import json

import pytest

NAME = "Example Person"
ORG = "Liaquat University of Medical & Health Sciences"


@pytest.fixture(autouse=True)
def _no_paid_search(monkeypatch):
    """Every test here runs with no search provider and no credentials."""
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    for key in ("BRAVE_API_KEY", "BING_API_KEY", "SERPER_API_KEY", "GITHUB_TOKEN", "HIBP_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


async def _prepared(api_client, case_id):
    """A case carrying a profile, an image and an analyst decision."""
    target = await api_client.post(
        f"/api/v1/cases/{case_id}/targets",
        json={
            "value": NAME,
            "type": "PERSON",
            "context": {"organizations": [ORG], "country": "Pakistan"},
        },
    )
    target_id = target.json()["id"]
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                {
                    "query": f'"{NAME}" site:linkedin.com',
                    "url": "https://www.linkedin.com/in/example-person",
                    "title": "Example Person",
                    "engine": "Google",
                },
                {
                    "query": f'"{NAME}" photo',
                    "url": "https://example.org/faculty",
                    "title": "Faculty",
                    "engine": "Google",
                    "image_url": "https://example.org/img/p.jpg",
                    "caption": "Faculty portrait",
                },
            ]
        },
    )
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": profile["id"],
            "decision": "NEEDS_REVIEW",
            "note": "Same city; no independent link yet.",
        },
    )
    return target_id, profile


# ------------------------------------------------------------------- reporting


async def test_the_json_report_carries_profiles_images_and_decisions(api_client, case_id):
    await _prepared(api_client, case_id)
    response = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    assert response.status_code == 200
    report = json.loads(response.text)

    assert len(report["social_profiles"]) == 1
    assert len(report["images"]) == 1
    assert len(report["analyst_decisions"]) == 1


async def test_report_profiles_keep_provenance_and_both_judgements(api_client, case_id):
    await _prepared(api_client, case_id)
    report = json.loads(
        (await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})).text
    )
    profile = report["social_profiles"][0]

    assert profile["platform"] == "linkedin"
    assert profile["evidence_class"] == "investigator_imported"
    assert profile["accessibility"] == "RESTRICTED"
    assert profile["fetch_note"]
    # Both claims present, and distinct.
    assert isinstance(profile["confidence"], float)
    assert profile["analyst_decision"] == "NEEDS_REVIEW"
    assert profile["analyst_note"]


async def test_report_images_carry_provenance_and_the_biometric_limit(api_client, case_id):
    await _prepared(api_client, case_id)
    report = json.loads(
        (await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})).text
    )
    image = report["images"][0]

    assert image["source_page_url"] == "https://example.org/faculty"
    assert image["fetch_state"] == "REFERENCE_ONLY"
    assert image["sha256"] is None, "an unfetched image must not claim a hash"
    assert image["origin"] == "manual_search_recon"
    assert "dimensions" in image and "redirects" in image
    # The limit travels with the data into the report.
    assert image["analysis"] == "none"
    assert image["biometric_matching"] is False


async def test_the_markdown_report_still_renders(api_client, case_id):
    await _prepared(api_client, case_id)
    response = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    assert response.status_code == 200
    assert NAME in response.text


async def test_a_report_never_claims_a_facial_match(api_client, case_id):
    await _prepared(api_client, case_id)
    for fmt in ("json", "md", "html"):
        text = (
            await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": fmt})
        ).text.lower()
        for phrase in ("face match", "facial match", "face matched", "biometric match"):
            assert phrase not in text, f"{fmt} report contained {phrase!r}"


# ------------------------------------------------------------------- zero cost


async def test_the_whole_workflow_runs_with_no_paid_provider(api_client, case_id):
    target_id, profile = await _prepared(api_client, case_id)

    plan = (
        await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    ).json()
    families = {item["family"] for item in plan["queries"]}
    assert {"social", "image", "anchor"} <= families

    assert (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert (await api_client.get(f"/api/v1/cases/{case_id}/candidates")).json()
    assert profile["confidence"] >= 0.0


async def test_the_employers_name_is_not_dropped_for_containing_a_screened_word(
    api_client, case_id
):
    """The acceptance organisation contains "Medical", which the screen refuses
    as a bare search term. Refusing to search for a stated employer because of
    that would make the platform useless to anyone working at a hospital."""
    target = await api_client.post(
        f"/api/v1/cases/{case_id}/targets",
        json={"value": NAME, "type": "PERSON", "context": {"organizations": [ORG]}},
    )
    plan = (
        await api_client.get(f"/api/v1/cases/{case_id}/targets/{target.json()['id']}/recon-queries")
    ).json()
    queries = [item["query"] for item in plan["queries"]]
    assert any(ORG in query for query in queries), queries
    # And the platform still refuses to compose such a term on its own.
    assert not any(
        query.lower().endswith(" medical") or " medical " in query.lower().replace(ORG.lower(), "")
        for query in queries
    )
