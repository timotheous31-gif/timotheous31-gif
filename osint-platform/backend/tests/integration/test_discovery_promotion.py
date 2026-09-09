"""Public data the collectors already retrieve must become visible evidence.

The gap this closes, taken from a real report: a GitHub candidate was found,
`profile_detail_fetched` was true, and the finished report still never mentioned
the account's avatar or listed the profile as a profile. Everything sat in a
JSON blob. A URL pasted in by hand produced *more* than the same URL discovered
automatically, which is backwards.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.models import ContactClassification, ContactType, ImageFetchState
from app.models.enums import FindingKind

NAME = "Example Person"
LOGIN = "example-dev"
API = "https://api.github.com"

SEARCH_BODY = {
    "total_count": 1,
    "items": [
        {
            "login": LOGIN,
            "html_url": f"https://github.com/{LOGIN}",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        }
    ],
}


def _profile(**overrides):
    body = {
        "login": LOGIN,
        "name": NAME,
        "html_url": f"https://github.com/{LOGIN}",
        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        "company": "Example Institute",
        "location": "Example City",
        "blog": "https://example.org/~person",
        "email": None,
        "bio": "Researcher",
        "public_repos": 7,
    }
    body.update(overrides)
    return body


async def _run_github(profile_body):
    """Run the collector against mocked GitHub and return its candidates."""
    from app.collectors.github_people import GitHubPeopleCollector
    from app.collectors.person import PersonContext
    from app.models.enums import TargetType
    from app.services.normalization import NormalizedTarget

    respx.get(f"{API}/search/users").mock(return_value=httpx.Response(200, json=SEARCH_BODY))
    respx.get(f"{API}/users/{LOGIN}").mock(return_value=httpx.Response(200, json=profile_body))
    # This account publishes no special profile repository. Mocked explicitly so
    # the tests below exercise the plain path deliberately rather than by
    # accident; the enrichment path has its own file.
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(return_value=httpx.Response(404))

    collector = GitHubPeopleCollector()
    target = NormalizedTarget(
        type=TargetType.PERSON,
        raw_input=NAME,
        value=NAME.lower(),
        attributes={"display_name": NAME, "context": {}},
    )
    candidates, _notes = await collector.find_candidates(NAME, target, PersonContext())
    return candidates


# ------------------------------------------------- the collector keeps the data


@respx.mock
async def test_the_collector_keeps_the_avatar_it_already_fetched():
    """Every GitHub account has one; it was being discarded outright."""
    candidates = await _run_github(_profile())
    assert candidates
    assert candidates[0].extra["avatar_url"] == "https://avatars.githubusercontent.com/u/1?v=4"


@respx.mock
async def test_an_explicitly_published_email_is_kept():
    candidates = await _run_github(_profile(email="person@example.org"))
    assert candidates[0].extra["public_email"] == "person@example.org"


@respx.mock
async def test_an_unpublished_email_stays_absent_and_is_never_derived():
    """GitHub returns null when the holder did not publish one. That is final."""
    candidates = await _run_github(_profile(email=None))
    extra = candidates[0].extra
    assert extra["public_email"] is None
    # Nothing anywhere may have invented an address from the name and a domain.
    blob = repr(candidates[0])
    assert "example.person@" not in blob
    assert "person@example-institute" not in blob


@respx.mock
async def test_company_location_and_blog_survive():
    candidate = (await _run_github(_profile()))[0]
    assert candidate.affiliations == ["Example Institute"]
    assert candidate.locations == ["Example City"]
    assert candidate.extra["blog"] == "https://example.org/~person"


# ------------------------------------------------------------------- promotion


def _finding(session, case_id, target_id, **data):
    from app.models import Finding

    payload = {
        "url": f"https://github.com/{LOGIN}",
        "source": "github_people",
        "source_label": "GitHub",
        "subject_value": NAME.lower(),
        "subject_name": NAME,
        "candidate_name": NAME,
        "identifiers": {"github_login": LOGIN},
        "extra": {},
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
        confidence=0.15,
        dedupe_key=f"gh:{payload['url']}",
    )
    session.add(finding)
    session.flush()
    return finding


async def _promoted(api_client, case_id, **extra):
    """Create a PERSON target and promote one GitHub finding against it."""
    from app.core.db import get_session_factory
    from app.models import Target
    from app.services.promotion import promote_finding

    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
    )
    target_id = response.json()["id"]

    import uuid as _uuid

    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        finding = _finding(session, _uuid.UUID(case_id), target.id, extra=extra)
        counts = promote_finding(
            session, case_id=_uuid.UUID(case_id), finding=finding, target=target
        )
        session.commit()
    return counts


async def test_a_github_candidate_becomes_a_social_profile(api_client, case_id):
    await _promoted(api_client, case_id, login=LOGIN)
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert len(profiles) == 1
    assert profiles[0]["platform"] == "github"
    assert profiles[0]["handle"] == LOGIN
    assert profiles[0]["collector"] == "github_people"
    assert profiles[0]["evidence_class"] == "api_fetched"


async def test_the_avatar_becomes_image_evidence_by_reference(api_client, case_id):
    """Recorded, but honestly: collection did not download those bytes."""
    await _promoted(api_client, case_id, avatar_url="https://avatars.githubusercontent.com/u/1?v=4")
    images = (await api_client.get(f"/api/v1/cases/{case_id}/images")).json()
    assert len(images) == 1
    image = images[0]
    assert image["fetch_state"] == "REFERENCE_ONLY"
    assert image["sha256"] is None
    assert image["source_page_url"] == f"https://github.com/{LOGIN}"
    assert image["social_profile_id"] is not None
    # And it is never presented as proof of who is depicted.
    assert image["attributes"]["biometric_matching"] is False
    assert image["attributes"]["analysis"] == "none"


async def test_a_published_email_becomes_a_public_contact(api_client, case_id):
    await _promoted(api_client, case_id, public_email="person@example.org")
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["contact_type"] == "EMAIL"
    assert contact["value"] == "person@example.org"
    assert contact["classification"] == "PUBLIC_SELF_PUBLISHED"
    assert contact["source_name"] == "GitHub"
    assert "not derived" in " ".join(contact["confidence_reasons"]).lower()
    assert contact["extraction_reason"]


async def test_no_contact_is_invented_when_the_source_published_none(api_client, case_id):
    await _promoted(api_client, case_id, login=LOGIN)
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    assert contacts == []


async def test_a_personal_site_becomes_a_website_contact(api_client, case_id):
    await _promoted(api_client, case_id, blog="https://example.org/~person")
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    assert [item["contact_type"] for item in contacts] == ["WEBSITE"]
    assert contacts[0]["value"] == "https://example.org/~person"


async def test_a_social_link_in_the_blog_field_becomes_a_profile_not_a_contact(api_client, case_id):
    """A link to an account is a profile; only a real site is a website."""
    await _promoted(api_client, case_id, blog="https://x.com/exampleuser")
    contacts = (await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()
    profiles = (await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()
    assert contacts == []
    assert "twitter" in {item["platform"] for item in profiles}


# ------------------------------------------------------------------ dedupe


async def test_promoting_the_same_finding_twice_does_not_duplicate(api_client, case_id):
    """Re-running an investigation strengthens the record, never multiplies it."""
    import uuid as _uuid

    from app.core.db import get_session_factory
    from app.models import Target
    from app.services.promotion import promote_finding

    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": NAME, "type": "PERSON"}
    )
    target_id = response.json()["id"]
    with get_session_factory()() as session:
        target = session.get(Target, _uuid.UUID(target_id))
        finding = _finding(
            session,
            _uuid.UUID(case_id),
            target.id,
            extra={
                "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                "public_email": "person@example.org",
            },
        )
        for _ in range(3):
            promote_finding(session, case_id=_uuid.UUID(case_id), finding=finding, target=target)
        session.commit()

    assert len((await api_client.get(f"/api/v1/cases/{case_id}/social-profiles")).json()) == 1
    assert len((await api_client.get(f"/api/v1/cases/{case_id}/images")).json()) == 1
    assert len((await api_client.get(f"/api/v1/cases/{case_id}/public-contacts")).json()) == 1


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("github_people", ContactClassification.PUBLIC_SELF_PUBLISHED),
        ("orcid", ContactClassification.PUBLIC_PROFESSIONAL),
        ("openalex", ContactClassification.PUBLIC_PROFESSIONAL),
        ("manual_search_recon", ContactClassification.UNVERIFIED_PUBLIC_REFERENCE),
    ],
)
def test_classification_follows_provenance_not_plausibility(source, expected):
    from app.services.promotion import classification_for

    assert classification_for(source) is expected


def test_the_promotion_module_never_constructs_an_address():
    """The rule that keeps a guess from being printed beside real evidence."""
    import pathlib

    source = pathlib.Path("app/services/promotion.py").read_text("utf-8")
    constructions = ["'@' +", '"@" +', "%s@%s", "+ '@' +", '+ "@" +']
    for pattern in constructions:
        assert pattern not in source, pattern


def test_a_non_person_finding_promotes_nothing(db_session):
    from app.models import Case, Finding
    from app.services.promotion import promote_finding

    case = Case(name="x")
    db_session.add(case)
    db_session.flush()
    finding = Finding(
        case_id=case.id,
        kind=FindingKind.DNS_RECORD,
        title="t",
        data={"url": "https://example.org"},
        collector="dns",
        confidence=0.5,
        dedupe_key="d",
    )
    db_session.add(finding)
    db_session.flush()
    counts = promote_finding(db_session, case_id=case.id, finding=finding, target=None)
    assert counts == {"profiles": 0, "images": 0, "contacts": 0}


def test_image_fetch_state_enum_is_what_promotion_records():
    assert ImageFetchState.REFERENCE_ONLY.value == "REFERENCE_ONLY"
    assert ContactType.EMAIL.value == "EMAIL"
