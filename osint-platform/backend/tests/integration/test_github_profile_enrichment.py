"""The special GitHub profile repository, end to end.

The gap this closes: an account was reported as a bare login while its own
profile page stated the person's post, their employer and their field. GitHub
publishes that page through a documented API, and the platform was not asking
for it.

Everything below runs against fixtures. No test touches the real account, and
every value here is a documentation domain or a mock handle.
"""

from __future__ import annotations

import base64
import uuid as _uuid

import httpx
import pytest
import respx

# Imported here rather than inside the helpers so the collector registers its
# rate limit at collection time — before ``_fast_github`` replaces it.
from app.collectors.github_people import GitHubPeopleCollector
from app.models.enums import FindingKind

NAME = "Example Person"
SEARCHED = "Example Person"
LOGIN = "example-person"
API = "https://api.github.com"
README_URL = f"https://github.com/{LOGIN}/{LOGIN}/blob/main/README.md"

README = """# Hi, I'm Example Person Dass

Lecturer in English (BPS-17), Government of Sindh, Pakistan - Applied Linguist

- Email: e.person@example.edu
- Website: https://example.org/example-person
- [LinkedIn](https://www.linkedin.com/in/example-person)

![Portrait](https://example.com/portrait.jpg)
![build](https://img.shields.io/badge/build-passing-green)
"""


@pytest.fixture(autouse=True)
def _fast_github():
    """Run the collector's four calls without waiting out its rate limit.

    The limit itself is not under test here — ``test_collector_framework``
    covers it — and four sequential two-second waits per test would add two
    minutes to CI for nothing.
    """
    from app.core.http import register_provider
    from app.core.ratelimit import RateLimit

    register_provider("github_people", RateLimit(requests=50, per_seconds=0.01, concurrency=4))
    yield


def _repo(**overrides):
    body = {
        "full_name": f"{LOGIN}/{LOGIN}",
        "html_url": f"https://github.com/{LOGIN}/{LOGIN}",
        "private": False,
        "description": "My public profile",
        "homepage": "https://example.org/example-person",
        "default_branch": "main",
    }
    body.update(overrides)
    return body


def _readme_body(text: str = README):
    return {
        "name": "README.md",
        "sha": "deadbeef",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "html_url": README_URL,
    }


def _mock_github(*, readme=README, repo_status=200, readme_status=200, profile=None):
    respx.get(f"{API}/search/users").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "login": LOGIN,
                        "html_url": f"https://github.com/{LOGIN}",
                        "avatar_url": "https://avatars.githubusercontent.com/u/2?v=4",
                    }
                ],
            },
        )
    )
    respx.get(f"{API}/users/{LOGIN}").mock(
        return_value=httpx.Response(
            200,
            json=(
                profile
                if profile is not None
                else {
                    "login": LOGIN,
                    "name": "Example Person Dass",
                    "html_url": f"https://github.com/{LOGIN}",
                    "avatar_url": "https://avatars.githubusercontent.com/u/2?v=4",
                    "company": None,
                    "location": None,
                    "blog": "",
                    "email": None,
                    "bio": "",
                    "public_repos": 3,
                }
            ),
        )
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(
            repo_status, json=_repo() if repo_status == 200 else {"message": "Not Found"}
        )
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(
            readme_status,
            json=_readme_body(readme) if readme_status == 200 else {"message": "Not Found"},
        )
    )


async def _collect(context: dict | None = None):
    from app.collectors.base import CollectorContext
    from app.models.enums import TargetType
    from app.services.normalization import NormalizedTarget

    target = NormalizedTarget(
        type=TargetType.PERSON,
        raw_input=SEARCHED,
        value=SEARCHED.lower(),
        attributes={"display_name": SEARCHED, "context": context or {}},
    )
    collector = GitHubPeopleCollector()
    return await collector.collect(target, CollectorContext(case_id=_uuid.uuid4())), target


# --------------------------------------------------------------- the collector


@respx.mock
async def test_the_special_profile_repository_is_discovered_and_read():
    _mock_github()
    result, _ = await _collect()
    data = result.findings[0].data
    assert data["profile_repository"]["exists"] is True
    assert data["profile_repository"]["full_name"] == f"{LOGIN}/{LOGIN}"
    assert data["readme_url"] == README_URL
    assert data["readme_sha"] == "deadbeef"


@respx.mock
async def test_the_stated_occupation_employer_field_and_country_are_extracted():
    _mock_github()
    result, _ = await _collect()
    facts = {fact["kind"]: fact["value"] for fact in result.findings[0].data["readme_facts"]}
    assert facts["occupation"] == "Lecturer in English (BPS-17)"
    assert facts["employer"] == "Government of Sindh"
    assert facts["professional_field"] == "Applied Linguist"
    assert facts["location"] == "Pakistan"


@respx.mock
async def test_a_country_association_never_becomes_a_nationality():
    _mock_github()
    result, _ = await _collect()
    fact = next(
        item for item in result.findings[0].data["readme_facts"] if item["kind"] == "location"
    )
    assert fact["label"] == "Public geographic association"
    assert "not a claim of nationality" in fact["interpretation"].lower()
    # The words appear only inside the disclaimer that rules them out; no fact
    # anywhere asserts one.
    facts = result.findings[0].data["readme_facts"]
    for item in facts:
        claim = " ".join(
            str(value) for key, value in item.items() if key != "interpretation"
        ).lower()
        for forbidden in ("nationality", "citizenship", "citizen of", "passport"):
            assert forbidden not in claim


@respx.mock
async def test_the_declared_name_is_related_to_the_searched_name_not_substituted():
    _mock_github()
    result, target = await _collect()
    data = result.findings[0].data
    assert data["searched_name"] == SEARCHED
    assert data["declared_name"] == "Example Person Dass"
    assert data["name_relationship"]["relationship"] == "extends_searched_name"
    assert "unchanged" in data["name_relationship"]["explanation"]
    # The target itself is untouched: the investigator named the subject.
    assert target.attributes["display_name"] == SEARCHED
    assert target.value == SEARCHED.lower()


@respx.mock
async def test_stated_facts_feed_the_anchor_engine_so_context_can_corroborate():
    """An employer the README states must be comparable to one supplied."""
    _mock_github()
    result, _ = await _collect({"organizations": ["Government of Sindh"], "country": "Pakistan"})
    data = result.findings[0].data
    assert "affiliation" in data["corroborated_by"]
    assert "location" in data["corroborated_by"]


@respx.mock
async def test_a_supplied_github_username_is_looked_up_when_the_search_misses_it():
    respx.get(f"{API}/search/users").mock(
        return_value=httpx.Response(200, json={"total_count": 0, "items": []})
    )
    respx.get(f"{API}/users/{LOGIN}").mock(
        return_value=httpx.Response(
            200,
            json={
                "login": LOGIN,
                "name": "Example Person Dass",
                "html_url": f"https://github.com/{LOGIN}",
                "avatar_url": "https://avatars.githubusercontent.com/u/2?v=4",
            },
        )
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(200, json=_readme_body())
    )
    result, _ = await _collect({"github_username": LOGIN})
    assert len(result.findings) == 1
    data = result.findings[0].data
    assert data["login"] == LOGIN
    assert "github_username" in data["corroborated_by"]
    assert data["readme_url"] == README_URL


@respx.mock
async def test_a_missing_profile_repository_is_reported_and_changes_nothing_else():
    _mock_github(repo_status=404)
    result, _ = await _collect()
    data = result.findings[0].data
    assert data["profile_repository"]["exists"] is False
    assert data["readme_url"] is None
    assert data["readme_facts"] == []
    # The candidate is still produced from what the profile API did return.
    assert data["login"] == LOGIN
    assert any("no special profile repository" in note for note in result.notes)


@respx.mock
async def test_a_rate_limited_readme_says_so_rather_than_reporting_nothing_found():
    _mock_github(readme_status=403)
    result, _ = await _collect()
    assert any("rate-limited" in note for note in result.notes)
    assert result.findings[0].data["readme_facts"] == []


@respx.mock
async def test_a_malformed_readme_is_survived():
    _mock_github(readme="\x00\x01 ``` |||| \x02")
    result, _ = await _collect()
    assert result.findings[0].data["readme_facts"] == []


@respx.mock
async def test_enrichment_never_enumerates_repositories():
    _mock_github()
    listing = respx.get(f"{API}/users/{LOGIN}/repos").mock(
        return_value=httpx.Response(200, json=[])
    )
    await _collect()
    assert listing.call_count == 0


# ----------------------------------------------------------------- promotion


def _finding(session, case_id, target_id, data):
    from app.models import Finding

    payload = {
        "url": f"https://github.com/{LOGIN}",
        "source": "github_people",
        "source_label": "GitHub",
        "subject_value": SEARCHED.lower(),
        "subject_name": SEARCHED,
        "candidate_name": "Example Person Dass",
        "identifiers": {"github_login": LOGIN},
        "login": LOGIN,
    }
    payload.update(data)
    finding = Finding(
        case_id=case_id,
        target_id=target_id,
        kind=FindingKind.PERSON_CANDIDATE,
        title=NAME,
        summary="",
        data=payload,
        collector="github_people",
        source_url=payload["url"],
        confidence=0.83,
        dedupe_key=f"gh:{payload['url']}",
    )
    session.add(finding)
    session.flush()
    return finding


async def _promote(api_client, case_id, *, times: int = 1, readme: str = README):
    """Collect against fixtures, then promote the finding as the engine does."""
    from app.core.db import get_session_factory
    from app.models import Target
    from app.services.promotion import promote_finding

    _mock_github(readme=readme)
    result, _ = await _collect({"github_username": LOGIN})
    data = dict(result.findings[0].data)

    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": SEARCHED, "type": "PERSON"}
    )
    target_id = response.json()["id"]

    counts = {}
    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        finding = _finding(session, _uuid.UUID(case_id), target.id, data)
        for _ in range(times):
            counts = promote_finding(
                session, case_id=_uuid.UUID(case_id), finding=finding, target=target
            )
        session.commit()
    return counts


@respx.mock
async def test_the_readme_email_becomes_a_self_published_contact(api_client, case_id):
    await _promote(api_client, case_id)
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    email = next(item for item in contacts if item["contact_type"] == "EMAIL")
    assert email["value"] == "e.person@example.edu"
    # Provenance, not plausibility: an .edu address on a personal profile is
    # still self-published.
    assert email["classification"] == "PUBLIC_SELF_PUBLISHED"
    assert email["source_url"] == README_URL
    assert email["evidence_id"] is not None, "a contact must cite a stored artefact"
    assert email["retrieved_at"] is not None
    assert "never constructs an address" in email["extraction_reason"]


@respx.mock
async def test_no_email_is_invented_when_the_readme_publishes_none(api_client, case_id):
    await _promote(
        api_client,
        case_id,
        readme="# Example Person Dass\n\nLecturer in English, Example University\n",
    )
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    assert not [item for item in contacts if item["contact_type"] == "EMAIL"]
    blob = str(contacts)
    assert "example.person@" not in blob
    assert "@example-university" not in blob


@respx.mock
async def test_a_linked_social_account_is_promoted_but_stays_conservative(api_client, case_id):
    await _promote(api_client, case_id)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    linkedin = next(item for item in profiles if item["platform"] == "linkedin")
    assert linkedin["source_url"] == README_URL
    assert any("linked from" in reason.lower() for reason in linkedin["match_reasons"])
    # A published link is provenance, never proof of ownership.
    assert linkedin["confidence"] <= 0.30
    assert "github_username" not in linkedin["corroborated_by"]


@respx.mock
async def test_a_readme_image_becomes_page_context_and_nothing_more(api_client, case_id):
    await _promote(api_client, case_id)
    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    portrait = next(item for item in images if "portrait" in item["image_url"])
    assert portrait["source_page_url"] == README_URL
    assert portrait["fetch_state"] == "REFERENCE_ONLY"
    assert portrait["sha256"] is None
    assert portrait["attributes"]["facial_recognition"] is False
    assert portrait["attributes"]["biometric_matching"] is False
    # Generated build badges are pictures of a number, not evidence.
    assert not any("shields.io" in item["image_url"] for item in images)


@respx.mock
async def test_the_profile_carries_its_self_description_and_both_names(api_client, case_id):
    await _promote(api_client, case_id)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    github = next(item for item in profiles if item["platform"] == "github")
    facts = {fact["kind"]: fact["value"] for fact in github["profile_facts"]}
    assert facts["occupation"] == "Lecturer in English (BPS-17)"
    assert facts["employer"] == "Government of Sindh"
    assert facts["location"] == "Pakistan"
    assert github["searched_name"] == SEARCHED
    assert github["declared_name"] == "Example Person Dass"
    assert github["detail_source_url"] == README_URL


@respx.mock
async def test_promotion_is_idempotent(api_client, case_id):
    """Re-running an investigation strengthens the record, never multiplies it."""
    await _promote(api_client, case_id, times=3)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert len([item for item in profiles if item["platform"] == "github"]) == 1
    assert len([item for item in profiles if item["platform"] == "linkedin"]) == 1
    assert len([item for item in contacts if item["value"] == "e.person@example.edu"]) == 1
    assert len([item for item in images if "portrait" in item["image_url"]]) == 1


@respx.mock
async def test_the_target_name_is_never_rewritten_by_a_declared_name(api_client, case_id):
    await _promote(api_client, case_id)
    body = (await api_client.get(f"/api/v1/cases/{case_id}/targets")).json()
    targets = body["items"] if isinstance(body, dict) else body
    target = targets[0]
    assert target["normalized_value"] == SEARCHED.lower()
    assert target["attributes"]["display_name"] == SEARCHED
    assert "Dass" not in target["raw_input"]


@respx.mock
async def test_the_report_shows_the_profile_without_raw_json(api_client, case_id):
    await _promote(api_client, case_id)
    markdown = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    ).text
    assert "## Public profiles and contacts" in markdown
    assert "Declared name: Example Person Dass" in markdown
    assert "Lecturer in English (BPS-17)" in markdown
    assert "Government of Sindh" in markdown
    assert "Public geographic association: Pakistan" in markdown
    assert "e.person@example.edu" in markdown
    assert "Automated confidence:" in markdown
    assert "Analyst decision:" in markdown


@respx.mock
async def test_an_analyst_decision_never_edits_the_automated_confidence(api_client, case_id):
    await _promote(api_client, case_id)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    github = next(item for item in profiles if item["platform"] == "github")
    before = github["confidence"]

    await api_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={
            "subject_type": "SOCIAL_PROFILE",
            "subject_id": github["id"],
            "decision": "NEEDS_REVIEW",
            "note": "Employer stated on the README; verify against the department roll.",
        },
    )
    after = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    judged = next(item for item in after if item["platform"] == "github")
    assert judged["confidence"] == before
    assert judged["decision"]["decision"] == "NEEDS_REVIEW"
