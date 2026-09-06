"""Public GitHub collector.

Uses only the documented public REST API and only endpoints that return data
GitHub already shows to anonymous visitors: profiles, public repositories,
languages, topics and public commit metadata.

Deliberate limits:

* private repositories are never requested, and a token — if configured — is
  expected to be read-only;
* commit *metadata* is collected (message subject, date, author login), never
  patch content;
* if a public repository description or homepage happens to expose something
  credential-shaped, it is reported as ``POTENTIAL_SECRET_EXPOSURE`` with the
  value masked. The platform never stores or displays the credential.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

from app.collectors.base import (
    BaseCollector,
    CollectorConfiguration,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

MAX_REPOS = 100
MAX_COMMITS = 30


@register_collector
class GitHubCollector(BaseCollector):
    """Collect public profile, repository and commit metadata from GitHub."""

    name = "github"
    version = "1.0.0"
    description = "Public GitHub profile, repositories, languages, topics and commit metadata."
    supported_targets = [
        TargetType.USERNAME,
        TargetType.REPOSITORY,
        TargetType.ORGANIZATION,
        TargetType.SOCIAL_PROFILE,
    ]
    #: Works unauthenticated at 60 requests/hour; a read-only token raises that.
    requires_api_key = False
    rate_limit = RateLimit(requests=1, per_seconds=1.2, concurrency=2)
    timeout = 20.0
    run_timeout = 90.0
    retry = RetryPolicy(attempts=2, base_delay=2.0)
    default_confidence = 0.9
    source_attribution = "GitHub public REST API"

    def _token(self) -> str:
        """The configured token, or ``""`` when there is none.

        A present-but-blank secret is treated as absent. Settings already
        normalises those to ``None``; this second check keeps the collector
        correct even when it is constructed with a hand-built ``Settings``.
        """
        secret = self.settings.github_token
        return secret.get_secret_value().strip() if secret is not None else ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._token()
        # Never emit `Authorization: Bearer ` with no credential: it is not a
        # weaker request, it is an illegal header value that fails the request
        # before it is sent. Unauthenticated access is a supported mode here.
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def is_available(self) -> tuple[bool, str]:
        """Always available; a token only raises the rate limit."""
        return True, ""

    def configuration(self) -> CollectorConfiguration:
        """Report the authentication mode, never the token."""
        authenticated = bool(self._token())
        return CollectorConfiguration(
            optional_settings=["GITHUB_TOKEN"],
            # Nothing is *required*: anonymous access to these endpoints is a
            # supported mode, not a degraded one.
            configured=authenticated,
            mode="authenticated" if authenticated else "unauthenticated",
            detail=(
                "GITHUB_TOKEN is set; the public REST API allows 5000 requests/hour."
                if authenticated
                else (
                    "No GITHUB_TOKEN set. Public GitHub endpoints are queried "
                    "anonymously at 60 requests/hour. Set a read-only token to "
                    "raise that limit."
                )
            ),
        )

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        if target.type is TargetType.REPOSITORY:
            return await self._collect_repository(target)
        return await self._collect_account(target)

    # ------------------------------------------------------------- account

    def _login(self, target: NormalizedTarget) -> str:
        if target.type is TargetType.SOCIAL_PROFILE:
            if target.attributes.get("platform") != "github":
                return ""
            return str(target.attributes.get("handle", ""))
        if target.type is TargetType.ORGANIZATION:
            return str(target.attributes.get("display_name", target.value)).replace(" ", "")
        return target.value

    async def _collect_account(self, target: NormalizedTarget) -> CollectorResult:
        login = self._login(target)
        result = CollectorResult(stats={"login": login, "authenticated": bool(self._token())})
        if not login:
            result.notes.append(f"{target.value} is not a GitHub identity")
            return result

        api = self.settings.github_api_url.rstrip("/")
        profile_url = f"{api}/users/{login}"
        response = await http.get(
            profile_url,
            provider=self.name,
            headers=self._headers(),
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        result.stats["profile_status"] = response.status_code

        if response.status_code == 404:
            result.notes.append(f"No public GitHub account named {login!r}")
            return result
        if response.status_code == 403:
            raise CollectorError(
                "GitHub rejected the request (rate limit or forbidden). "
                "Set GITHUB_TOKEN to a read-only token to raise the limit."
            )
        if not response.ok:
            raise CollectorError(f"GitHub returned HTTP {response.status_code} for {login}")

        profile = response.json()
        profile_payload = RawPayload(
            source_url=profile_url, content=profile, status_code=response.status_code
        )
        for draft in self._normalize_profile(profile_payload):
            result.add(draft, profile_payload)

        repos_url = f"{api}/users/{login}/repos"
        orgs_url = f"{api}/users/{login}/orgs"
        repos_response, orgs_response = await asyncio.gather(
            http.get(
                repos_url,
                provider=self.name,
                headers=self._headers(),
                params={"per_page": MAX_REPOS, "sort": "updated", "type": "owner"},
                timeout=self.timeout,
                cache_ttl=self.settings.cache_ttl_seconds,
            ),
            http.get(
                orgs_url,
                provider=self.name,
                headers=self._headers(),
                timeout=self.timeout,
                cache_ttl=self.settings.cache_ttl_seconds,
            ),
            return_exceptions=True,
        )

        if isinstance(repos_response, http.HttpResponse) and repos_response.ok:
            repos = repos_response.json()
            payload = RawPayload(source_url=repos_url, content=repos)
            for draft in self._normalize_repositories(repos, login):
                result.add(draft, payload)
            result.stats["repositories"] = len(repos) if isinstance(repos, list) else 0
        elif isinstance(repos_response, BaseException):
            result.notes.append(f"Repository listing failed: {type(repos_response).__name__}")

        if isinstance(orgs_response, http.HttpResponse) and orgs_response.ok:
            orgs = orgs_response.json()
            payload = RawPayload(source_url=orgs_url, content=orgs)
            for draft in self._normalize_orgs(orgs, login):
                result.add(draft, payload)
        elif isinstance(orgs_response, BaseException):
            result.notes.append(f"Organisation listing failed: {type(orgs_response).__name__}")

        return result

    # ---------------------------------------------------------- repository

    async def _collect_repository(self, target: NormalizedTarget) -> CollectorResult:
        owner = str(target.attributes.get("owner", ""))
        name = str(target.attributes.get("name", ""))
        result = CollectorResult(
            stats={"repository": f"{owner}/{name}", "authenticated": bool(self._token())}
        )
        if target.attributes.get("platform") != "github" or not owner or not name:
            result.notes.append(f"{target.value} is not a public GitHub repository reference")
            return result

        api = self.settings.github_api_url.rstrip("/")
        repo_url = f"{api}/repos/{owner}/{name}"
        response = await http.get(
            repo_url,
            provider=self.name,
            headers=self._headers(),
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        result.stats["status"] = response.status_code
        if response.status_code == 404:
            result.notes.append(f"No public repository {owner}/{name}")
            return result
        if not response.ok:
            raise CollectorError(f"GitHub returned HTTP {response.status_code} for {owner}/{name}")

        repo = response.json()
        payload = RawPayload(source_url=repo_url, content=repo, status_code=response.status_code)
        for draft in self._normalize_repositories([repo], owner):
            result.add(draft, payload)

        commits_url = f"{api}/repos/{owner}/{name}/commits"
        try:
            commits_response = await http.get(
                commits_url,
                provider=self.name,
                headers=self._headers(),
                params={"per_page": MAX_COMMITS},
                timeout=self.timeout,
                cache_ttl=self.settings.cache_ttl_seconds,
            )
        except Exception as exc:
            result.notes.append(f"Commit listing failed: {type(exc).__name__}")
            return result

        if commits_response.ok:
            commits = commits_response.json()
            commit_payload = RawPayload(source_url=commits_url, content=commits)
            for draft in self._normalize_commits(commits, owner, name):
                result.add(draft, commit_payload)
            result.stats["commits"] = len(commits) if isinstance(commits, list) else 0
        return result

    # ---------------------------------------------------------- normalisers

    def _normalize_profile(self, raw: RawPayload) -> list[FindingDraft]:
        profile: dict[str, Any] = raw.content
        login = str(profile.get("login", ""))
        created = _parse(profile.get("created_at"))
        account_type = str(profile.get("type", "User"))

        data = {
            "login": login,
            "account_type": account_type,
            "name": profile.get("name"),
            "company": profile.get("company"),
            "blog": profile.get("blog") or None,
            "location_text": profile.get("location"),
            "bio": (str(profile.get("bio"))[:500] if profile.get("bio") else None),
            "public_repos": profile.get("public_repos", 0),
            "followers": profile.get("followers", 0),
            "following": profile.get("following", 0),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
            "html_url": profile.get("html_url"),
            "hireable": profile.get("hireable"),
        }
        findings = [
            FindingDraft(
                kind=FindingKind.CODE_PROFILE,
                title=f"GitHub profile {login}",
                summary=(
                    f"{account_type} account with {data['public_repos']} public repositories"
                    + (f", created {created:%Y-%m-%d}" if created else "")
                ),
                data=data,
                source_url=profile.get("html_url") or raw.source_url,
                confidence=0.95,
                confidence_reasons=["Retrieved from GitHub's public API for this account"],
                # A profile is self-published, but free-text fields can name a
                # person; the privacy filter decides what is shown.
                classification=Classification.PERSONAL,
                observed_at=created,
                dedupe_key=f"github:profile:{login}",
            )
        ]

        blog = str(profile.get("blog") or "").strip()
        if blog:
            findings.append(
                FindingDraft(
                    kind=FindingKind.CODE_PROFILE,
                    title=f"{login} publishes a website link",
                    summary=f"The GitHub profile links to {blog}",
                    data={"login": login, "url": blog, "relation": "profile_links_to_site"},
                    source_url=profile.get("html_url") or raw.source_url,
                    # Self-declared on the account itself: a strong link.
                    confidence=0.9,
                    confidence_reasons=[
                        "The account owner published this link on their own profile"
                    ],
                    classification=Classification.PUBLIC,
                    dedupe_key=f"github:blog:{login}:{blog}",
                )
            )
        return findings

    def _normalize_repositories(self, repos: object, owner: str) -> list[FindingDraft]:
        if not isinstance(repos, list):
            return []
        findings: list[FindingDraft] = []
        for repo in repos[:MAX_REPOS]:
            if not isinstance(repo, dict) or repo.get("private"):
                continue
            full_name = str(repo.get("full_name") or f"{owner}/{repo.get('name')}")
            created = _parse(repo.get("created_at"))
            findings.append(
                FindingDraft(
                    kind=FindingKind.REPOSITORY,
                    title=full_name,
                    summary=(
                        f"{repo.get('language') or 'Unspecified language'} repository"
                        f" with {repo.get('stargazers_count', 0)} star(s)"
                    ),
                    data={
                        "full_name": full_name,
                        "owner": (repo.get("owner") or {}).get("login", owner),
                        "description": (
                            str(repo.get("description"))[:500] if repo.get("description") else None
                        ),
                        "language": repo.get("language"),
                        "topics": repo.get("topics", []),
                        "homepage": repo.get("homepage") or None,
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "is_fork": bool(repo.get("fork")),
                        "created_at": repo.get("created_at"),
                        "pushed_at": repo.get("pushed_at"),
                        "default_branch": repo.get("default_branch"),
                        "license": (repo.get("license") or {}).get("spdx_id"),
                        "html_url": repo.get("html_url"),
                    },
                    source_url=repo.get("html_url"),
                    confidence=0.95,
                    confidence_reasons=["Listed as public by GitHub's own API"],
                    classification=Classification.PUBLIC,
                    observed_at=created,
                    dedupe_key=f"github:repo:{full_name.lower()}",
                )
            )
            findings.extend(_secret_exposure_findings(repo, full_name))
        return findings

    def _normalize_orgs(self, orgs: object, login: str) -> list[FindingDraft]:
        if not isinstance(orgs, list):
            return []
        findings = []
        for org in orgs:
            if not isinstance(org, dict) or not org.get("login"):
                continue
            findings.append(
                FindingDraft(
                    kind=FindingKind.ORGANIZATION_MEMBERSHIP,
                    title=f"{login} is a public member of {org['login']}",
                    summary="Membership is published on the account's public profile",
                    data={
                        "login": login,
                        "organization": org["login"],
                        "description": org.get("description"),
                        "html_url": f"https://github.com/{org['login']}",
                    },
                    source_url=f"https://github.com/orgs/{org['login']}/people",
                    confidence=0.9,
                    confidence_reasons=["The account holder chose to make this membership public"],
                    classification=Classification.PUBLIC,
                    dedupe_key=f"github:org:{login}:{org['login']}",
                )
            )
        return findings

    def _normalize_commits(self, commits: object, owner: str, repo: str) -> list[FindingDraft]:
        """Commit *metadata* only — subjects and dates, never patch content."""
        if not isinstance(commits, list) or not commits:
            return []

        authors: dict[str, int] = {}
        dates: list[datetime] = []
        for item in commits[:MAX_COMMITS]:
            if not isinstance(item, dict):
                continue
            commit = item.get("commit") or {}
            author = (item.get("author") or {}).get("login") or (commit.get("author") or {}).get(
                "name"
            )
            if author:
                authors[str(author)] = authors.get(str(author), 0) + 1
            when = _parse((commit.get("author") or {}).get("date"))
            if when:
                dates.append(when)

        if not dates:
            return []
        dates.sort()
        return [
            FindingDraft(
                kind=FindingKind.COMMIT_ACTIVITY,
                title=f"Public commit activity in {owner}/{repo}",
                summary=(
                    f"{len(dates)} recent public commit(s) from {len(authors)} author(s), "
                    f"{dates[0]:%Y-%m-%d} to {dates[-1]:%Y-%m-%d}"
                ),
                data={
                    "repository": f"{owner}/{repo}",
                    "commit_count": len(dates),
                    "authors": dict(sorted(authors.items(), key=lambda kv: -kv[1])),
                    "first_commit": dates[0].isoformat(),
                    "latest_commit": dates[-1].isoformat(),
                    "active_days": len({when.date().isoformat() for when in dates}),
                },
                source_url=f"https://github.com/{owner}/{repo}/commits",
                confidence=0.9,
                confidence_reasons=["Derived from public commit metadata exposed by the API"],
                classification=Classification.PUBLIC,
                observed_at=dates[-1],
                dedupe_key=f"github:commits:{owner}/{repo}",
            )
        ]


def _secret_exposure_findings(repo: dict[str, Any], full_name: str) -> list[FindingDraft]:
    """Flag credential-shaped strings in public repository metadata.

    The matched value is never stored. Only the category, the location and the
    discovery time are recorded, so an investigator can responsibly notify the
    owner without the platform holding a live credential.
    """
    from app.privacy.secrets import scan_text

    findings: list[FindingDraft] = []
    for field in ("description", "homepage"):
        value = repo.get(field)
        if not value:
            continue
        for match in scan_text(str(value)):
            findings.append(
                FindingDraft(
                    kind=FindingKind.POTENTIAL_SECRET_EXPOSURE,
                    title=f"Possible {match.category} exposed in {full_name}",
                    summary=(
                        "A credential-shaped string appears in public repository metadata. "
                        "The value is not stored by this platform."
                    ),
                    data={
                        "repository": full_name,
                        "location": f"repository.{field}",
                        "secret_type": match.category,
                        "value": "[REDACTED SECRET]",
                        "commit": repo.get("default_branch"),
                        "status": "unverified",
                    },
                    source_url=repo.get("html_url"),
                    confidence=match.confidence,
                    confidence_reasons=[
                        f"Matches the published format of a {match.category}",
                        "Format match only — the credential has not been tested or used",
                    ],
                    classification=Classification.RESTRICTED,
                    dedupe_key=f"github:secret:{full_name}:{field}:{match.category}",
                )
            )
    return findings


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(str(value))
    except (ValueError, TypeError):
        return None
