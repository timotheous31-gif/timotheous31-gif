"""GitHub collector: public data only, secrets flagged but never stored."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.base import RawPayload
from app.collectors.github import GitHubCollector
from app.models.enums import Classification, FindingKind
from app.services.normalization import normalize_target


@pytest.fixture
def collector():
    return GitHubCollector()


def test_profile_normalisation(collector, fixture):
    raw = RawPayload(
        source_url="https://api.github.com/users/exampleuser", content=fixture("github_user.json")
    )
    findings = collector._normalize_profile(raw)
    profile = findings[0]
    assert profile.kind is FindingKind.CODE_PROFILE
    assert profile.data["login"] == "exampleuser"
    assert profile.data["public_repos"] == 8
    assert profile.observed_at.year == 2011
    # A profile carries free-text fields that may name a person.
    assert profile.classification is Classification.PERSONAL


def test_profile_blog_link_is_a_strong_self_published_signal(collector, fixture):
    raw = RawPayload(source_url="x", content=fixture("github_user.json"))
    link = next(
        f
        for f in collector._normalize_profile(raw)
        if f.data.get("relation") == "profile_links_to_site"
    )
    assert link.data["url"] == "https://example.com"
    assert link.confidence >= 0.85


def test_repositories_are_normalised(collector, fixture):
    findings = collector._normalize_repositories(fixture("github_repos.json"), "exampleuser")
    repos = [f for f in findings if f.kind is FindingKind.REPOSITORY]
    names = {f.data["full_name"] for f in repos}
    assert "exampleuser/Hello-World" in names
    hello = next(f for f in repos if f.data["full_name"] == "exampleuser/Hello-World")
    assert hello.data["language"] == "Python"
    assert hello.data["topics"] == ["example", "documentation"]
    assert hello.data["license"] == "MIT"


def test_private_repositories_are_never_collected(collector, fixture):
    findings = collector._normalize_repositories(fixture("github_repos.json"), "exampleuser")
    assert not any("private-notes" in str(f.data) for f in findings)


def test_credential_in_public_metadata_is_flagged_but_masked(collector, fixture):
    findings = collector._normalize_repositories(fixture("github_repos.json"), "exampleuser")
    exposure = next(f for f in findings if f.kind is FindingKind.POTENTIAL_SECRET_EXPOSURE)

    assert exposure.data["secret_type"] == "AWS access key id"
    assert exposure.data["value"] == "[REDACTED SECRET]"
    assert exposure.data["repository"] == "exampleuser/leaky-config"
    assert exposure.classification is Classification.RESTRICTED
    # The actual credential must appear nowhere in the finding.
    assert "AKIA1234567890ABCDEF" not in str(exposure.data)
    assert "AKIA1234567890ABCDEF" not in (exposure.summary or "")
    assert "AKIA1234567890ABCDEF" not in exposure.title


def test_organisation_membership(collector, fixture):
    findings = collector._normalize_orgs(fixture("github_orgs.json"), "exampleuser")
    assert findings[0].kind is FindingKind.ORGANIZATION_MEMBERSHIP
    assert findings[0].data["organization"] == "exampleorg"


def test_commit_activity_is_metadata_only(collector, fixture):
    findings = collector._normalize_commits(
        fixture("github_commits.json"), "exampleuser", "Hello-World"
    )
    activity = findings[0]
    assert activity.kind is FindingKind.COMMIT_ACTIVITY
    assert activity.data["commit_count"] == 3
    assert activity.data["authors"]["exampleuser"] == 2
    assert activity.data["latest_commit"].startswith("2025-01-09")
    # No commit message bodies or diffs are retained.
    assert "message" not in str(activity.data)


def test_commits_without_dates_yield_nothing(collector):
    assert collector._normalize_commits([{"commit": {}}], "o", "r") == []


@respx.mock
async def test_collect_account_gathers_profile_repos_and_orgs(
    collector, collector_ctx, fixture, mock_http
):
    respx.get("https://api.github.com/users/exampleuser").mock(
        return_value=httpx.Response(200, json=fixture("github_user.json"))
    )
    respx.get("https://api.github.com/users/exampleuser/repos").mock(
        return_value=httpx.Response(200, json=fixture("github_repos.json"))
    )
    respx.get("https://api.github.com/users/exampleuser/orgs").mock(
        return_value=httpx.Response(200, json=fixture("github_orgs.json"))
    )

    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    kinds = {f.kind for f in result.findings}
    assert FindingKind.CODE_PROFILE in kinds
    assert FindingKind.REPOSITORY in kinds
    assert FindingKind.ORGANIZATION_MEMBERSHIP in kinds
    assert result.stats["repositories"] == 3


@respx.mock
async def test_missing_account_is_a_note(collector, collector_ctx, mock_http):
    respx.get("https://api.github.com/users/nobody").mock(return_value=httpx.Response(404))
    result = await collector.collect(normalize_target("@nobody"), collector_ctx)
    assert result.findings == []
    assert result.notes


@respx.mock
async def test_rate_limited_account_raises_with_guidance(collector, collector_ctx, mock_http):
    from app.core.errors import CollectorError

    respx.get("https://api.github.com/users/exampleuser").mock(
        return_value=httpx.Response(403, json={"message": "rate limit exceeded"})
    )
    with pytest.raises(CollectorError, match="GITHUB_TOKEN"):
        await collector.collect(normalize_target("@exampleuser"), collector_ctx)


@respx.mock
async def test_repository_target_collects_commits(collector, collector_ctx, fixture, mock_http):
    respx.get("https://api.github.com/repos/exampleuser/Hello-World").mock(
        return_value=httpx.Response(200, json=fixture("github_repos.json")[0])
    )
    respx.get("https://api.github.com/repos/exampleuser/Hello-World/commits").mock(
        return_value=httpx.Response(200, json=fixture("github_commits.json"))
    )
    result = await collector.collect(
        normalize_target("https://github.com/exampleuser/Hello-World"), collector_ctx
    )
    kinds = {f.kind for f in result.findings}
    assert FindingKind.REPOSITORY in kinds
    assert FindingKind.COMMIT_ACTIVITY in kinds


@respx.mock
async def test_repository_listing_failure_leaves_profile_intact(
    collector, collector_ctx, fixture, mock_http
):
    respx.get("https://api.github.com/users/exampleuser").mock(
        return_value=httpx.Response(200, json=fixture("github_user.json"))
    )
    respx.get("https://api.github.com/users/exampleuser/repos").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.get("https://api.github.com/users/exampleuser/orgs").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = await collector.collect(normalize_target("@exampleuser"), collector_ctx)
    assert any(f.kind is FindingKind.CODE_PROFILE for f in result.findings)
    assert any("Repository listing failed" in note for note in result.notes)


@respx.mock
async def test_token_is_sent_as_bearer_when_configured(collector_ctx, mock_http, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_readonlytoken0000000000000000000000")
    reset_settings_cache()
    try:
        collector = GitHubCollector()
        route = respx.get("https://api.github.com/users/exampleuser").mock(
            return_value=httpx.Response(404)
        )
        collector_ctx.settings = collector.settings
        await collector.collect(normalize_target("@exampleuser"), collector_ctx)
        assert route.calls[0].request.headers["authorization"].startswith("Bearer ghp_")
    finally:
        reset_settings_cache()


def test_gitlab_repository_target_is_declined(collector, fixture):
    import asyncio

    target = normalize_target("https://gitlab.com/group/project")
    result = asyncio.run(collector._collect_repository(target))
    assert result.findings == []
    assert result.notes
