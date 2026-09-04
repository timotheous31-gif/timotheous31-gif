"""Curated list of public platforms for username presence checks.

Every entry must satisfy four conditions before it belongs here:

1. the profile page is served to anonymous visitors — no login, no bypass;
2. a documented, stable URL shape identifies a profile;
3. checking it requires a single unauthenticated request;
4. the platform is a *publishing* platform (code, writing, discussion), not a
   private social network or a dating/health/financial service, where mere
   presence is sensitive information about a person.

The list is deliberately small. Breadth here trades directly against both
politeness and the risk of building a profile-aggregation tool.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Platform:
    """One checkable public platform."""

    key: str
    display_name: str
    #: ``{username}`` is substituted with the normalised handle.
    url_template: str
    #: Optional API/JSON endpoint that answers existence more cheaply.
    probe_template: str | None = None
    #: HTTP status meaning "this profile exists".
    exists_status: tuple[int, ...] = (200,)
    #: HTTP status meaning "no such profile".
    missing_status: tuple[int, ...] = (404,)
    #: A body substring that means "not found" despite a 200 response.
    missing_marker: str | None = None
    #: How much a hit here contributes to a confidence score on its own.
    base_confidence: float = 0.5
    category: str = "general"

    def profile_url(self, username: str) -> str:
        return self.url_template.format(username=username)

    def probe_url(self, username: str) -> str:
        template = self.probe_template or self.url_template
        return template.format(username=username)


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="github",
        display_name="GitHub",
        url_template="https://github.com/{username}",
        probe_template="https://api.github.com/users/{username}",
        base_confidence=0.6,
        category="code",
    ),
    Platform(
        key="gitlab",
        display_name="GitLab",
        url_template="https://gitlab.com/{username}",
        probe_template="https://gitlab.com/api/v4/users?username={username}",
        base_confidence=0.55,
        category="code",
    ),
    Platform(
        key="hackernews",
        display_name="Hacker News",
        url_template="https://news.ycombinator.com/user?id={username}",
        probe_template="https://hacker-news.firebaseio.com/v0/user/{username}.json",
        missing_marker="null",
        base_confidence=0.5,
        category="discussion",
    ),
    Platform(
        key="reddit",
        display_name="Reddit",
        url_template="https://www.reddit.com/user/{username}",
        probe_template="https://www.reddit.com/user/{username}/about.json",
        base_confidence=0.5,
        category="discussion",
    ),
    Platform(
        key="devto",
        display_name="DEV Community",
        url_template="https://dev.to/{username}",
        probe_template="https://dev.to/api/users/by_username?url={username}",
        base_confidence=0.5,
        category="writing",
    ),
    Platform(
        key="keybase",
        display_name="Keybase",
        url_template="https://keybase.io/{username}",
        probe_template="https://keybase.io/_/api/1.0/user/lookup.json?username={username}",
        base_confidence=0.6,
        category="identity",
    ),
    Platform(
        key="pypi",
        display_name="PyPI",
        url_template="https://pypi.org/user/{username}/",
        base_confidence=0.55,
        category="code",
    ),
    Platform(
        key="npm",
        display_name="npm",
        url_template="https://www.npmjs.com/~{username}",
        probe_template="https://registry.npmjs.org/-/user/org.couchdb.user:{username}",
        base_confidence=0.55,
        category="code",
    ),
    Platform(
        key="mastodon_social",
        display_name="mastodon.social",
        url_template="https://mastodon.social/@{username}",
        probe_template="https://mastodon.social/api/v1/accounts/lookup?acct={username}",
        base_confidence=0.45,
        category="social",
    ),
)

PLATFORMS_BY_KEY: dict[str, Platform] = {platform.key: platform for platform in PLATFORMS}
