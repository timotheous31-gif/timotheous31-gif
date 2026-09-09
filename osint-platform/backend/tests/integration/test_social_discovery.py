"""Social discovery, and the three defects the acceptance test found.

The one that matters most is the first: a report showed a GitHub *finding* at
0.70 saying the handle matched, beside the *promoted profile* for the same
account at 0.15 saying no anchors were supplied. Both cannot be true of the
same evidence. Everything in the first section exists to keep them from
disagreeing again.
"""

from __future__ import annotations

import base64
import uuid as _uuid

import httpx
import pytest
import respx
from sqlalchemy import select

# Imported at module scope so the collector registers its rate limit before
# ``_fast_github`` replaces it.
from app.collectors.github_people import GitHubPeopleCollector
from app.models.enums import FindingKind

NAME = "Example Person"
LOGIN = "example-person"
API = "https://api.github.com"
PROFILE_URL = f"https://github.com/{LOGIN}"
README_URL = f"https://github.com/{LOGIN}/{LOGIN}/blob/main/README.md"

#: The acceptance README, including the sentence that produced
#: "Professional field: undergraduate".
ACCEPTANCE_README = """# Example Person Dass

Lecturer in English (BPS-17), Government of Sindh, Pakistan - Applied Linguist

I'm an English lecturer and applied linguist with 6+ years of teaching
experience across secondary, undergraduate, and postgraduate levels in Pakistan.

- [LinkedIn](https://www.linkedin.com/in/example-person)
- [YouTube](https://www.youtube.com/@example-person)
- [Instagram](https://www.instagram.com/example.person)

![Portrait](https://example.com/portrait.jpg)
"""


@pytest.fixture(autouse=True)
def _fast_github(mock_http):
    """Skip the collector's deliberate two-second spacing; it is not under test."""
    from app.core.http import register_provider
    from app.core.ratelimit import RateLimit

    register_provider("github_people", RateLimit(requests=50, per_seconds=0.01, concurrency=4))
    yield


def _mock_github(readme: str = ACCEPTANCE_README) -> None:
    respx.get(f"{API}/search/users").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "login": LOGIN,
                        "html_url": PROFILE_URL,
                        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                    }
                ],
            },
        )
    )
    respx.get(f"{API}/users/{LOGIN}").mock(
        return_value=httpx.Response(
            200,
            json={
                "login": LOGIN,
                "name": "Example Person Dass",
                "html_url": PROFILE_URL,
                "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                "company": None,
                "location": None,
                "blog": "",
                "email": None,
                "bio": "",
                "public_repos": 2,
            },
        )
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": f"{LOGIN}/{LOGIN}",
                "html_url": f"https://github.com/{LOGIN}/{LOGIN}",
                "private": False,
                "description": None,
                "homepage": None,
                "default_branch": "main",
            },
        )
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "README.md",
                "sha": "cafe",
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "html_url": README_URL,
            },
        )
    )


async def _collect(context: dict | None = None, readme: str = ACCEPTANCE_README):
    from app.collectors.base import CollectorContext
    from app.models.enums import TargetType
    from app.services.normalization import NormalizedTarget

    _mock_github(readme)
    target = NormalizedTarget(
        type=TargetType.PERSON,
        raw_input=NAME,
        value=NAME.lower(),
        attributes={"display_name": NAME, "context": context or {}},
    )
    result = await GitHubPeopleCollector().collect(target, CollectorContext(case_id=_uuid.uuid4()))
    return result.findings[0]


def _persist(session, case_id, target, draft):
    from app.models import Finding

    finding = Finding(
        case_id=case_id,
        target_id=target.id,
        kind=FindingKind.PERSON_CANDIDATE,
        title=NAME,
        summary=draft.summary,
        data=draft.data,
        collector="github_people",
        source_url=draft.data["url"],
        confidence=draft.confidence,
        dedupe_key=draft.dedupe_key,
    )
    session.add(finding)
    session.flush()
    return finding


async def _run(api_client, case_id, *, context: dict | None, readme: str = ACCEPTANCE_README):
    """Collect and promote one GitHub candidate, as the engine does."""
    from app.core.db import get_session_factory
    from app.models import Target
    from app.services.promotion import promote_finding

    draft = await _collect(context, readme)

    body = {"value": NAME, "type": "PERSON"}
    if context:
        body["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=body)
    assert response.status_code in (200, 201), response.text
    target_id = response.json()["id"]

    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        finding = _persist(session, _uuid.UUID(case_id), target, draft)
        promote_finding(session, case_id=_uuid.UUID(case_id), finding=finding, target=target)
        session.commit()
    return draft, target_id


async def _rerun_with_anchor(api_client, case_id, target_id, context):
    """Re-collect and re-promote the same profile after the anchors changed."""
    from app.core.db import get_session_factory
    from app.models import Finding, Target
    from app.services.promotion import promote_finding

    draft = await _collect(context)
    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        target.attributes = {**dict(target.attributes or {}), "context": context}
        row = session.scalars(
            select(Finding).where(
                Finding.case_id == _uuid.UUID(case_id),
                Finding.kind == FindingKind.PERSON_CANDIDATE,
            )
        ).first()
        assert row is not None
        row.data = draft.data
        row.confidence = draft.confidence
        session.flush()
        promote_finding(session, case_id=_uuid.UUID(case_id), finding=row, target=target)
        session.commit()
    return draft


async def _profiles(api_client, case_id):
    return (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()


def _github(profiles):
    return next(item for item in profiles if item["platform"] == "github")


# ------------------------------------------------- bug 1: stale promoted profile


@respx.mock
async def test_a_reran_profile_carries_the_current_confidence(api_client, case_id):
    """0.15 name-only, then the anchor arrives. The profile must move with it."""
    _draft, target_id = await _run(api_client, case_id, context=None)
    before = _github(await _profiles(api_client, case_id))
    assert before["corroborated_by"] == []

    after_draft = await _rerun_with_anchor(
        api_client, case_id, target_id, {"github_username": LOGIN}
    )
    after = _github(await _profiles(api_client, case_id))

    assert after["confidence"] > before["confidence"]
    assert after["confidence"] == pytest.approx(
        after_draft.confidence, abs=1e-9
    ), "the promoted profile and its finding must agree on the same evidence"
    assert "github_username" in after["corroborated_by"]


@respx.mock
async def test_stale_mismatch_reasons_do_not_survive_a_refresh(api_client, case_id):
    _draft, target_id = await _run(api_client, case_id, context=None)
    stale = _github(await _profiles(api_client, case_id))["mismatch_reasons"]
    assert any("anchors" in reason for reason in stale)

    await _rerun_with_anchor(api_client, case_id, target_id, {"github_username": LOGIN})
    reasons = " ".join(_github(await _profiles(api_client, case_id))["mismatch_reasons"])
    assert "No anchors were supplied" not in reasons
    assert "is not one you supplied" not in reasons


@respx.mock
async def test_stale_match_reasons_do_not_survive_a_refresh(api_client, case_id):
    _draft, target_id = await _run(api_client, case_id, context={"github_username": LOGIN})
    assert any(
        "username you supplied" in reason
        for reason in _github(await _profiles(api_client, case_id))["match_reasons"]
    )
    # The anchor is withdrawn: the reason for it must go with it.
    await _rerun_with_anchor(api_client, case_id, target_id, {})
    after = _github(await _profiles(api_client, case_id))
    assert after["corroborated_by"] == []
    assert not any("username you supplied" in reason for reason in after["match_reasons"])


@respx.mock
async def test_a_refresh_does_not_duplicate_the_profile(api_client, case_id):
    _draft, target_id = await _run(api_client, case_id, context=None)
    await _rerun_with_anchor(api_client, case_id, target_id, {"github_username": LOGIN})
    profiles = await _profiles(api_client, case_id)
    assert len([item for item in profiles if item["platform"] == "github"]) == 1


@respx.mock
async def test_an_analyst_decision_and_note_survive_an_automated_refresh(api_client, case_id):
    _draft, target_id = await _run(api_client, case_id, context=None)
    profile = _github(await _profiles(api_client, case_id))

    await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": profile["id"],
            "decision": "NEEDS_REVIEW",
            "note": "Verify the employer against the department roll.",
        },
    )
    await _rerun_with_anchor(api_client, case_id, target_id, {"github_username": LOGIN})

    after = _github(await _profiles(api_client, case_id))
    assert after["decision"]["decision"] == "NEEDS_REVIEW"
    assert after["decision"]["note"] == "Verify the employer against the department roll."
    # And the decision did not hold the score down.
    assert "github_username" in after["corroborated_by"]


@respx.mock
async def test_readme_facts_survive_a_refresh_that_read_nothing_new(api_client, case_id):
    """Provenance merges even though correlation is replaced."""
    _draft, target_id = await _run(api_client, case_id, context=None)
    await _rerun_with_anchor(api_client, case_id, target_id, {"github_username": LOGIN})
    after = _github(await _profiles(api_client, case_id))
    kinds = {fact["kind"] for fact in after["profile_facts"]}
    assert {"occupation", "employer", "location"} <= kinds
    assert after["detail_source_url"] == README_URL


# ------------------------------------------------------- bug 2: README levels


@respx.mock
async def test_a_teaching_level_is_never_a_professional_field(api_client, case_id):
    draft = await _collect({"github_username": LOGIN})
    facts = {fact["kind"]: fact["value"] for fact in draft.data["readme_facts"]}
    fields = [
        fact["value"] for fact in draft.data["readme_facts"] if fact["kind"] == "professional_field"
    ]
    assert "undergraduate" not in [value.lower() for value in fields]
    assert not any(
        value.lower() in {"secondary", "postgraduate", "graduate", "doctoral"} for value in fields
    )
    # The four facts the acceptance scenario expects, and only those.
    assert facts["occupation"] == "Lecturer in English (BPS-17)"
    assert facts["employer"] == "Government of Sindh"
    assert facts["location"] == "Pakistan"
    assert facts["professional_field"] == "Applied Linguist"


@respx.mock
async def test_applied_linguist_is_still_a_professional_field(api_client, case_id):
    draft = await _collect({"github_username": LOGIN})
    fields = [
        fact["value"] for fact in draft.data["readme_facts"] if fact["kind"] == "professional_field"
    ]
    assert fields == ["Applied Linguist"]


# ------------------------------------------------------ bug 3: report images


@respx.mock
async def test_the_markdown_report_renders_an_image_card(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "### Public image evidence" in markdown
    assert "avatars.githubusercontent.com" in markdown
    assert "- State: REFERENCE_ONLY" in markdown
    assert "SHA-256: unavailable because the image bytes were not fetched" in markdown
    assert "does not independently establish identity" in markdown


@respx.mock
async def test_a_reference_only_image_is_not_embedded_by_default(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "![" not in markdown, "a static export must not make the reader fetch a remote image"


@respx.mock
async def test_embed_images_draws_render_safe_evidence(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    markdown = (
        await api_client.get(
            f"/api/v1/cases/{case_id}/report",
            params={"format": "md", "embed_images": "true"},
        )
    ).text
    assert "![" in markdown
    assert "https://avatars.githubusercontent.com/u/1?v=4)" in markdown


@respx.mock
async def test_no_sha_is_invented_for_bytes_nobody_read(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    image = report["images"][0]
    assert image["fetch_state"] == "REFERENCE_ONLY"
    assert image["sha256"] is None
    assert image["render_safe"] is True
    assert image["biometric_matching"] is False
    assert "does not independently establish identity" in image["disclaimer"]


@respx.mock
async def test_an_image_carries_its_candidate_and_profile_context(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    image = report["images"][0]
    assert image["platform_label"] == "GitHub"
    assert image["handle"] == LOGIN
    assert image["profile_url"] == PROFILE_URL


# ------------------------------------------------------- report consistency


@respx.mock
async def test_the_finding_and_its_promoted_profile_never_disagree(api_client, case_id):
    _draft, _target_id = await _run(api_client, case_id, context={"github_username": LOGIN})
    report = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    profile = next(item for item in report["social_profiles"] if item["platform"] == "github")
    candidate = next(
        item
        for item in report["findings"]
        if item["kind"] == "PERSON_CANDIDATE" and item["data"]["url"] == PROFILE_URL
    )
    assert profile["confidence"] == pytest.approx(candidate["confidence"], abs=1e-4)
    assert set(profile["corroborated_by"]) == set(candidate["data"]["corroborated_by"])


@respx.mock
async def test_the_report_names_how_each_profile_was_found(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "How it was found:" in markdown
    assert "linked from another public page" in markdown


# ------------------------------------------------ layer B: published links


@respx.mock
async def test_readme_links_become_profiles_with_readme_provenance(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    profiles = await _profiles(api_client, case_id)
    platforms = {item["platform"] for item in profiles}
    assert {"linkedin", "youtube", "instagram"} <= platforms
    linkedin = next(item for item in profiles if item["platform"] == "linkedin")
    assert linkedin["source_url"] == README_URL
    assert linkedin["discovery_method"] == "published_link"
    assert "README" in (linkedin["discovered_from"] or "")


@respx.mock
async def test_a_published_link_does_not_confirm_ownership(api_client, case_id):
    await _run(api_client, case_id, context={"github_username": LOGIN})
    profiles = await _profiles(api_client, case_id)
    github = _github(profiles)
    for platform in ("linkedin", "youtube", "instagram"):
        linked = next(item for item in profiles if item["platform"] == platform)
        assert linked["confidence"] < github["confidence"]
        assert linked["confidence"] <= 0.30
        assert "github_username" not in linked["corroborated_by"]
        assert any("not proof of ownership" in reason for reason in linked["match_reasons"])
