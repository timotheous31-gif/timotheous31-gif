"""GitHub public user search for PERSON targets.

Kept separate from the ``github`` collector, which answers "what is at this
login?". This one answers a different question — "which public accounts give
this name?" — and the distinction matters: the earlier bug was a person's name
being turned into a login by deleting its spaces, which is a guess. Here the
name goes to GitHub's own ``in:fullname`` search and GitHub decides what
matches, so every candidate is something GitHub actually indexed.

Works unauthenticated. A read-only ``GITHUB_TOKEN`` only raises the rate limit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.collectors.base import CollectorConfiguration, CollectorContext, RawPayload
from app.collectors.github_profile import ProfileEnrichment, enrich_login
from app.collectors.person import (
    PersonCandidate,
    PersonContext,
    PersonSourceCollector,
    name_relationship,
    normalize_handle,
)
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import CollectorError, CollectorUnavailable
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit, RetryPolicy
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

SEARCH_LIMIT = 20
#: Profiles fetched for detail. Search results carry only a login, and the
#: fields that corroborate a candidate — name, company, location — need a
#: profile request each. Unauthenticated callers get 60 requests/hour, so this
#: stays small deliberately.
PROFILE_DETAIL_LIMIT = 6
#: Accounts whose special profile repository is inspected. Two documented API
#: calls each — the repository and its README — so this stays small. A supplied
#: ``github_username`` is enriched first, because that is the account the
#: investigator already believes in.
README_ENRICHMENT_LIMIT = 3


@register_collector
class GitHubPeopleCollector(PersonSourceCollector):
    """Find public GitHub accounts whose profile name carries a name."""

    name = "github_people"
    version = "1.0.0"
    description = "Public GitHub accounts whose profile name matches a person's name."
    source_label = "GitHub"
    rate_limit = RateLimit(requests=1, per_seconds=2.0, concurrency=2)
    timeout = 20.0
    run_timeout = 120.0
    retry = RetryPolicy(attempts=2, base_delay=2.0)
    default_confidence = 0.2
    source_attribution = "GitHub public user search API"
    free_access_note = (
        "GitHub's public search works with no token at 10 searches/minute. "
        "An optional read-only GITHUB_TOKEN raises the limit."
    )

    def _token(self) -> str:
        secret = self.settings.github_token
        return secret.get_secret_value().strip() if secret is not None else ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def configuration(self) -> CollectorConfiguration:
        authenticated = bool(self._token())
        return CollectorConfiguration(
            optional_settings=["GITHUB_TOKEN"],
            configured=True,
            mode="authenticated" if authenticated else "free",
            detail=(
                "GITHUB_TOKEN is set, raising the search rate limit."
                if authenticated
                else self.free_access_note
            ),
        )

    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        api = self.settings.github_api_url.rstrip("/")
        search_url = f"{api}/search/users"
        response = await http.get(
            search_url,
            provider=self.name,
            # `in:fullname` searches the self-declared profile name, which is
            # what a person search means here. `type:user` keeps organisations
            # out of a list of candidate people.
            params={"q": f'"{name}" in:fullname type:user', "per_page": SEARCH_LIMIT},
            headers=self._headers(),
            timeout=self.timeout,
            retry=self.retry,
            cache_ttl=self.settings.cache_ttl_seconds,
        )
        if response.status_code in {403, 429}:
            raise CollectorUnavailable(
                "GitHub rate-limited the unauthenticated search. Set a read-only "
                "GITHUB_TOKEN to raise the limit, or retry later."
            )
        if not response.ok:
            raise CollectorError(f"GitHub search returned HTTP {response.status_code}")

        payload = response.json() or {}
        raw = RawPayload(source_url=search_url, content=payload, status_code=response.status_code)
        items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]

        notes: list[str] = []
        total = payload.get("total_count")
        if isinstance(total, int) and total > len(items):
            notes.append(
                f"GitHub reports {total} account(s) whose profile name matches {name!r}; "
                f"the first {len(items)} are shown."
            )

        context = PersonContext.from_target(target)
        anchor = normalize_handle(context.github_username or "")
        logins = [str(item.get("login", "")) for item in items]
        if anchor and anchor not in {login.lower() for login in logins}:
            # The investigator supplied this account. Looking it up is not a
            # guess — it is checking the anchor they already have, and a search
            # on a name is not obliged to surface it.
            supplied = await self._supplied_account(anchor, api)
            if supplied is not None:
                items.append(supplied)
                logins.append(anchor)
                notes.append(
                    f"The GitHub account you supplied (@{anchor}) was not in the name "
                    f"search results and was looked up directly."
                )
            else:
                notes.append(
                    f"The GitHub account you supplied (@{anchor}) could not be read "
                    f"(missing, private or rate-limited)."
                )

        profiles = await self._profiles(logins, api)
        if len(items) > PROFILE_DETAIL_LIMIT:
            notes.append(
                f"Profile detail (name, company, location) was fetched for the first "
                f"{PROFILE_DETAIL_LIMIT} accounts only, to stay inside GitHub's "
                f"unauthenticated rate limit. The rest are listed on their login alone."
            )

        enrichments = await self._enrichments(list(profiles), anchor, api)
        for login, enrichment in enrichments.items():
            if enrichment.readme_note:
                notes.append(f"@{login}: {enrichment.readme_note}")

        return (
            [self._candidate(item, profiles, enrichments, name, raw) for item in items],
            notes,
        )

    async def _supplied_account(self, login: str, api: str) -> dict[str, Any] | None:
        """Read one account the investigator named, as a search result would."""
        try:
            response = await http.get(
                f"{api}/users/{login}",
                provider=self.name,
                headers=self._headers(),
                timeout=self.timeout,
                cache_ttl=self.settings.cache_ttl_seconds,
            )
        # A supplied anchor that cannot be read is a gap in the evidence, not a
        # reason to abandon the search that is already in hand.
        except Exception:
            return None
        if not response.ok:
            return None
        body = response.json()
        if not isinstance(body, dict) or not body.get("login"):
            return None
        return {
            "login": str(body["login"]),
            "html_url": str(body.get("html_url") or f"https://github.com/{body['login']}"),
            "avatar_url": str(body.get("avatar_url") or ""),
        }

    async def _profiles(self, logins: list[str], api: str) -> dict[str, dict[str, Any]]:
        """Fetch a bounded number of profiles, tolerating individual failures."""
        wanted = [login for login in logins if login][:PROFILE_DETAIL_LIMIT]
        if not wanted:
            return {}

        async def fetch(login: str) -> tuple[str, dict[str, Any] | None]:
            try:
                response = await http.get(
                    f"{api}/users/{login}",
                    provider=self.name,
                    headers=self._headers(),
                    timeout=self.timeout,
                    cache_ttl=self.settings.cache_ttl_seconds,
                )
            except Exception:
                return login, None
            if not response.ok:
                return login, None
            body = response.json()
            return login, body if isinstance(body, dict) else None

        results = await asyncio.gather(*(fetch(login) for login in wanted))
        return {login: profile for login, profile in results if profile is not None}

    async def _enrichments(
        self, logins: list[str], anchor: str, api: str
    ) -> dict[str, ProfileEnrichment]:
        """Inspect the special profile repository for a bounded set of accounts.

        The account the investigator anchored on comes first: it is the one
        whose self-description they are actually asking about. Everything else
        is capped, because this is profile enrichment and not a GitHub crawl —
        no repository listing, and nothing found in a README is followed.
        """
        ordered = sorted(logins, key=lambda login: login.lower() != anchor)
        wanted = [login for login in ordered if login][:README_ENRICHMENT_LIMIT]
        if not wanted:
            return {}
        results = await asyncio.gather(
            *(
                enrich_login(
                    login,
                    api=api,
                    headers=self._headers(),
                    provider=self.name,
                    timeout=self.timeout,
                    retry=self.retry,
                    cache_ttl=self.settings.cache_ttl_seconds,
                )
                for login in wanted
            )
        )
        return {enrichment.login: enrichment for enrichment in results}

    def _candidate(
        self,
        item: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        enrichments: dict[str, ProfileEnrichment],
        searched_name: str,
        raw: RawPayload,
    ) -> PersonCandidate:
        login = str(item.get("login", ""))
        profile = profiles.get(login, {})
        enrichment = enrichments.get(login)

        declared_name = str(profile.get("name") or "").strip()
        company = str(profile.get("company") or "").strip()
        location = str(profile.get("location") or "").strip()
        blog = str(profile.get("blog") or "").strip()
        avatar_url = str(profile.get("avatar_url") or item.get("avatar_url") or "").strip()
        # GitHub returns `email` only when the account holder chose to publish
        # it. A null here means they did not, and that is the end of it: no
        # address is ever derived from a name and a domain.
        public_email = str(profile.get("email") or "").strip()

        # The profile README is the one place on GitHub where a person writes
        # about themselves. What it states is added to the fields the anchor
        # engine already compares, so a stated employer or country can
        # corroborate — or contradict — what the investigator supplied.
        readme_declared = ""
        affiliations = [company] if company else []
        locations = [location] if location else []
        occupations: list[str] = []
        if enrichment is not None:
            readme_declared = next(iter(enrichment.values("declared_name")), "")
            affiliations += [
                value for value in enrichment.values("employer") if value not in affiliations
            ]
            locations += [
                value for value in enrichment.values("location") if value not in locations
            ]
            occupations = enrichment.values("occupation") + enrichment.values("professional_field")

        name = declared_name or readme_declared or login
        summary = f"Public GitHub account @{login}"
        if declared_name:
            summary += f", profile name {declared_name!r}"
        if company:
            summary += f", company {company}"
        if occupations:
            summary += f", stated as {occupations[0]!r} on the profile README"
        if not profile:
            summary += " (profile detail not fetched)"

        extra: dict[str, Any] = {
            "login": login,
            "declared_name": declared_name or readme_declared or None,
            "searched_name": searched_name,
            # Recorded, never applied: a source's spelling of a name is that
            # source's claim, and the target keeps the name the investigator
            # gave it.
            "name_relationship": name_relationship(searched_name, declared_name or readme_declared),
            "profile_detail_fetched": bool(profile),
            "public_repos": profile.get("public_repos"),
            "blog": blog or None,
            # Promoted downstream into a SocialProfile, an ImageEvidence
            # record and a public contact. Every GitHub account has an
            # avatar; leaving it in the raw payload was the single largest
            # piece of public data this collector was discarding.
            "avatar_url": avatar_url or None,
            "public_email": public_email or None,
            "company": company or None,
            "location": location or None,
            "profile_url": str(item.get("html_url") or f"https://github.com/{login}"),
            "bio": str(profile.get("bio") or "").strip() or None,
            "occupations": occupations,
        }
        if enrichment is not None:
            extra.update(enrichment.to_dict())
            if enrichment.repository.description:
                extra["repository_description"] = enrichment.repository.description
            if enrichment.repository.homepage:
                extra["repository_homepage"] = enrichment.repository.homepage

        return PersonCandidate(
            url=str(item.get("html_url") or f"https://github.com/{login}"),
            # Fall back to the login when the profile was not fetched: claiming
            # the searched name as this account's name would beg the question.
            name=name,
            summary=summary,
            identifiers={"github_login": login},
            affiliations=affiliations,
            locations=locations,
            handles=[login],
            extra=extra,
            payload=raw,
        )
