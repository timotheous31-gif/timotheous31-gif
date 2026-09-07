"""The social and visual recon workflow, end to end over the API.

The properties defended here are the ones that stop the platform over-claiming:
a matching handle is not ownership, a name is weak, an analyst decision does not
edit what the platform computed, and an image is page context rather than proof
of who is in it.
"""

from __future__ import annotations

import uuid

import pytest

NAME = "Example Person"
ORG = "Liaquat University of Medical & Health Sciences"


async def _person(api_client, case_id, **context):
    payload = {"value": NAME, "type": "PERSON"}
    if context:
        payload["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _import(api_client, case_id, target_id, **overrides):
    result = {
        "query": f'"{NAME}" site:linkedin.com',
        "url": "https://www.linkedin.com/in/example-person",
        "title": "Example Person",
        "engine": "Google",
    }
    result.update(overrides)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results", json={"results": [result]}
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------- profiles from imports


async def test_an_imported_profile_url_becomes_a_social_profile(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)

    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert len(profiles) == 1
    assert profiles[0]["platform"] == "linkedin"
    assert profiles[0]["handle"] == "example-person"
    assert profiles[0]["evidence_class"] == "investigator_imported"


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://x.com/exampleuser", "twitter"),
        ("https://www.tiktok.com/@exampleuser", "tiktok"),
        ("https://www.instagram.com/exampleuser", "instagram"),
        ("https://www.facebook.com/example.person", "facebook"),
        ("https://www.youtube.com/@examplechannel", "youtube"),
    ],
)
async def test_each_required_platform_is_recorded(api_client, case_id, url, platform):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id, url=url)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert [item["platform"] for item in profiles] == [platform]


async def test_a_video_page_does_not_become_a_profile(api_client, case_id):
    """The category error that would invent an attribution from a path prefix."""
    target_id = await _person(api_client, case_id)
    await _import(
        api_client, case_id, target_id, url="https://www.tiktok.com/@someone/video/7100000000"
    )
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert profiles == []


async def test_a_blocked_platform_is_recorded_honestly(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    assert profile["server_fetchable"] is False
    assert profile["accessibility"] == "RESTRICTED"
    assert "anonymous" in (profile["fetch_note"] or "").lower()


# ------------------------------------------------------------------ correlation


async def test_a_supplied_username_anchors_a_profile(api_client, case_id):
    target_id = await _person(api_client, case_id, known_usernames=["exampleuser"])
    await _import(api_client, case_id, target_id, url="https://x.com/exampleuser")
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    assert "username" in profile["corroborated_by"]
    assert any("supplied" in reason for reason in profile["match_reasons"])


async def test_a_matching_handle_alone_is_not_treated_as_ownership(api_client, case_id):
    """Handles are reused, sold and coincidental."""
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id, url="https://x.com/exampleuser")
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    assert profile["corroborated_by"] == []
    assert any("not one you supplied" in reason for reason in profile["mismatch_reasons"])
    assert any("would not establish ownership" in reason for reason in profile["mismatch_reasons"])


async def test_a_name_alone_stays_weak(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    # The name-only rule's ceiling, far below anything that could merge identities.
    assert profile["confidence"] <= 0.30
    assert any("Nothing beyond the name" in reason for reason in profile["mismatch_reasons"])


async def test_an_unanchored_profile_says_no_anchors_were_supplied(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    assert any("No anchors were supplied" in reason for reason in profile["mismatch_reasons"])


async def test_manual_imports_use_the_same_anchor_logic_as_collectors(api_client, case_id):
    """The bug this pins: the manual path once scored differently from collection."""
    from app.collectors.person import PersonCandidate, PersonContext, anchor_matches

    target_id = await _person(api_client, case_id, github_username="octocat")
    await _import(api_client, case_id, target_id, url="https://github.com/octocat")
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]

    direct = anchor_matches(
        PersonCandidate(url="https://github.com/octocat", name=NAME, handles=["octocat"]),
        PersonContext(github_username="octocat"),
    )
    assert [kind for kind, _ in direct] == profile["corroborated_by"]
    assert "github_username" in profile["corroborated_by"]


# ---------------------------------------------------------------------- images


async def test_an_imported_image_is_reference_only_until_fetched(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(
        api_client,
        case_id,
        target_id,
        url="https://example.org/faculty",
        image_url="https://example.org/img/p.jpg",
        caption="Faculty portrait",
    )
    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert len(images) == 1
    assert images[0]["fetch_state"] == "REFERENCE_ONLY"
    assert images[0]["sha256"] is None
    assert images[0]["caption"] == "Faculty portrait"


async def test_every_image_carries_the_no_analysis_statement(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(
        api_client,
        case_id,
        target_id,
        url="https://example.org/page",
        image_url="https://example.org/p.jpg",
    )
    image = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()[0]
    assert image["attributes"]["analysis"] == "none"
    assert image["attributes"]["biometric_matching"] is False
    assert image["attributes"]["facial_recognition"] is False
    assert "no facial recognition" in image["attributes"]["interpretation"].lower()


async def test_fetching_an_image_on_a_blocked_platform_declines_rather_than_trying(
    api_client, case_id
):
    target_id = await _person(api_client, case_id)
    await _import(
        api_client,
        case_id,
        target_id,
        url="https://www.linkedin.com/in/example-person",
        image_url="https://media.licdn.example/photo.jpg",
    )
    image = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()[0]
    response = await api_client.post(f"/api/v1/cases/{case_id}/images/{image['id']}/fetch", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["fetch_state"] == "REFERENCE_ONLY"
    assert body["sha256"] is None
    assert "not fetched" in (body["fetch_note"] or "").lower()


# ------------------------------------------------------------ analyst decisions


@pytest.mark.parametrize("decision", ["CONFIRMED", "REJECTED", "UNRESOLVED", "NEEDS_REVIEW"])
async def test_every_decision_value_can_be_recorded(api_client, case_id, decision):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]

    response = await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": profile["id"],
            "decision": decision,
            "note": "Reviewed.",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["decision"] == decision


async def test_a_decision_never_changes_automated_confidence(api_client, case_id):
    """The requirement the separate table exists to guarantee."""
    target_id = await _person(api_client, case_id, known_usernames=["exampleuser"])
    await _import(api_client, case_id, target_id, url="https://x.com/exampleuser")
    before = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]

    await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": before["id"],
            "decision": "REJECTED",
            "note": "Different person.",
        },
    )
    after = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]

    assert after["confidence"] == before["confidence"]
    assert after["match_reasons"] == before["match_reasons"]
    assert after["mismatch_reasons"] == before["mismatch_reasons"]
    assert after["corroborated_by"] == before["corroborated_by"]
    # The judgement is attached, not merged into the score.
    assert after["decision"]["decision"] == "REJECTED"


async def test_withdrawing_a_decision_leaves_the_assessment_intact(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": profile["id"],
            "decision": "CONFIRMED",
        },
    )
    response = await api_client.delete(
        f"/api/v1/cases/{case_id}/decisions/SOCIAL_PROFILE/{profile['id']}"
    )
    assert response.status_code == 204
    after = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    assert after["decision"] is None
    assert after["confidence"] == profile["confidence"]


async def test_a_decision_about_something_not_in_the_case_is_refused(api_client, case_id):
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": str(uuid.uuid4()),
            "decision": "CONFIRMED",
        },
    )
    assert response.status_code == 422


async def test_recording_a_decision_twice_updates_rather_than_duplicates(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    profile = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()[0]
    for decision in ("NEEDS_REVIEW", "CONFIRMED"):
        await api_client.post(
            f"/api/v1/cases/{case_id}/decisions",
            json={
                "subject_type": "SOCIAL_PROFILE",
                "subject_id": profile["id"],
                "decision": decision,
            },
        )
    decisions = (await api_client.get(f"/api/v1/cases/{case_id}/decisions")).json()
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "CONFIRMED"


# --------------------------------------------------------------------- grouping


async def test_unattributed_evidence_is_still_visible(api_client, case_id):
    """Evidence nobody has assigned must not vanish from the investigation."""
    target_id = await _person(api_client, case_id)
    await _import(api_client, case_id, target_id)
    groups = (await api_client.get(f"/api/v1/cases/{case_id}/candidates")).json()
    unattributed = [item for item in groups if item["entity_id"] is None]
    assert len(unattributed) == 1
    assert len(unattributed[0]["social_profiles"]) == 1


async def test_images_are_grouped_by_candidate_and_source_not_by_appearance(api_client, case_id):
    target_id = await _person(api_client, case_id)
    await _import(
        api_client,
        case_id,
        target_id,
        url="https://example.org/a",
        image_url="https://example.org/1.jpg",
    )
    await _import(
        api_client,
        case_id,
        target_id,
        url="https://example.org/b",
        image_url="https://example.org/2.jpg",
    )
    groups = (await api_client.get(f"/api/v1/cases/{case_id}/candidates")).json()
    images = [image for group in groups for image in group["images"]]
    assert len(images) == 2
    # Each keeps its own source page: grouping is by provenance, never by looks.
    assert {image["source_page_url"] for image in images} == {
        "https://example.org/a",
        "https://example.org/b",
    }
