"""What can legitimately be done with each public platform.

Three questions kept coming up in three different places — can a logged-out
server read this platform's pages, does it publish a documented API, is a
``site:`` query worth generating — and each was answered by an ``if`` somewhere
in the code that needed it. That is how a platform ends up marked unfetchable
in the classifier while a collector still tries to fetch it.

So this module is the single answer. It does not restate what the two existing
tables already know: :mod:`app.collectors.social` owns URL *shape* (hosts,
handle prefixes, whether a fetch is expected to be refused) and
:mod:`app.collectors.platforms` owns *existence checks* (probe endpoints and
their status codes). This composes them and adds only what neither records —
whether a platform is worth a manual search query, and whether it publishes a
profile image we may reference.

A platform marked ``server_fetchable=False`` is a statement about that
platform's policy towards anonymous clients. It is a reason to record the URL
and stop, never a reason to try something else: no login, no session, no proxy,
no evasion. A block is not evidence that a profile is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict
from urllib.parse import urlsplit

from app.collectors.platforms import PLATFORMS_BY_KEY, Platform
from app.collectors.social import PLATFORMS as URL_PLATFORMS
from app.collectors.social import SocialPlatform


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """One platform, and what this codebase may legitimately do with it."""

    platform: str
    display_name: str
    #: Hosts that belong to the platform, without a ``www.`` prefix.
    domains: tuple[str, ...]
    #: How a handle becomes a public profile URL, when a documented shape
    #: exists. ``None`` where the platform has no stable per-person URL.
    profile_url_pattern: str | None
    #: A logged-out server request is expected to be answered. False means the
    #: platform refuses anonymous automated access; the URL is recorded from its
    #: shape and nothing is fetched.
    server_fetchable: bool
    #: A documented public API answers questions about this platform.
    public_api_available: bool
    #: Worth a ``site:`` query in the manual search plan.
    manual_search_supported: bool
    #: A single unauthenticated request can confirm whether a handle exists.
    handle_check_supported: bool
    #: The platform publishes a profile image URL we may record as evidence.
    image_reference_supported: bool
    #: ``site:`` filters for the manual search plan, narrowest first.
    search_filters: tuple[str, ...] = ()
    notes: str = ""
    #: Set when the platform refuses anonymous server-side access, so a report
    #: can say why nothing was fetched.
    fetch_note: str = ""
    #: The key in :mod:`app.collectors.platforms`, when a probe exists.
    probe_key: str | None = None

    def profile_url(self, handle: str) -> str | None:
        """The public profile URL for ``handle``, or ``None`` if unknown."""
        cleaned = (handle or "").strip().lstrip("@")
        if not cleaned or not self.profile_url_pattern:
            return None
        return self.profile_url_pattern.format(handle=cleaned)


class _Extra(TypedDict, total=False):
    """What neither existing table records, per platform."""

    profile_url_pattern: str
    search_filters: tuple[str, ...]
    image_reference_supported: bool
    public_api_available: bool
    notes: str
    probe_key: str


#: What neither existing table records, declared once per platform. Keys are the
#: classifier's platform keys, so the three tables cannot drift apart silently —
#: a test asserts every key here exists in the classifier.
_EXTRA: dict[str, _Extra] = {
    "linkedin": {
        "profile_url_pattern": "https://www.linkedin.com/in/{handle}",
        "search_filters": ("linkedin.com/in", "linkedin.com"),
        "image_reference_supported": False,
        "notes": (
            "Refuses anonymous server-side requests. Discoverable through manual "
            "search; the URL is recorded from its shape and never fetched."
        ),
    },
    "instagram": {
        "profile_url_pattern": "https://www.instagram.com/{handle}",
        "search_filters": ("instagram.com",),
        "image_reference_supported": False,
        "notes": "Requires an authenticated session for profile content. Manual search only.",
    },
    "facebook": {
        "profile_url_pattern": "https://www.facebook.com/{handle}",
        "search_filters": ("facebook.com",),
        "image_reference_supported": False,
        "notes": "Gates profile content behind a login for anonymous clients.",
    },
    "youtube": {
        "profile_url_pattern": "https://www.youtube.com/@{handle}",
        "search_filters": ("youtube.com/@", "youtube.com"),
        "image_reference_supported": True,
        "notes": "Channel pages are served to logged-out visitors.",
    },
    "twitter": {
        "profile_url_pattern": "https://x.com/{handle}",
        "search_filters": ("x.com", "twitter.com"),
        "image_reference_supported": False,
        "notes": "Requires an authenticated session for profile content. Manual search only.",
    },
    "tiktok": {
        "profile_url_pattern": "https://www.tiktok.com/@{handle}",
        "search_filters": ("tiktok.com/@", "tiktok.com"),
        "image_reference_supported": False,
        "notes": "Blocks most anonymous server-side clients. Manual search only.",
    },
    "snapchat": {
        "profile_url_pattern": "https://www.snapchat.com/add/{handle}",
        "search_filters": ("snapchat.com",),
        "image_reference_supported": False,
        "notes": "Limited public profile surface; refuses most anonymous requests.",
    },
    "reddit": {
        "profile_url_pattern": "https://www.reddit.com/user/{handle}",
        "search_filters": ("reddit.com/user", "reddit.com"),
        "image_reference_supported": True,
        "notes": "Public JSON for user pages, subject to rate limiting.",
    },
    "github": {
        "profile_url_pattern": "https://github.com/{handle}",
        "search_filters": ("github.com",),
        "image_reference_supported": True,
        "notes": (
            "Fully documented public API, including the special profile "
            "repository and its README."
        ),
    },
    "gitlab": {
        "profile_url_pattern": "https://gitlab.com/{handle}",
        "search_filters": ("gitlab.com",),
        "image_reference_supported": True,
        "notes": "Documented public API for user lookup.",
    },
    "orcid": {
        "profile_url_pattern": "https://orcid.org/{handle}",
        "search_filters": ("orcid.org",),
        "image_reference_supported": False,
        # ORCID's public API is reached by the `orcid` collector rather than by
        # a handle probe: an ORCID iD is not a username, so "does this handle
        # exist" is not a question the registry can ask on its own.
        "public_api_available": True,
        "notes": (
            "Public researcher registry with a documented API, queried by the orcid "
            "collector. An iD is not a handle, so there is no username check."
        ),
    },
    "pypi": {
        "profile_url_pattern": "https://pypi.org/user/{handle}/",
        "search_filters": ("pypi.org/user",),
        "image_reference_supported": False,
        "notes": "Public user pages; no documented per-user API.",
    },
    "mastodon": {
        "profile_url_pattern": "https://mastodon.social/@{handle}",
        "search_filters": ("mastodon.social",),
        "image_reference_supported": True,
        "notes": "Public API per instance. Only mastodon.social is recognised by shape.",
        "probe_key": "mastodon_social",
    },
}

#: Platforms with a public existence check but no URL-shape entry, so the
#: registry covers them too rather than leaving a hole between the two tables.
_PROBE_ONLY: tuple[str, ...] = ("keybase", "hackernews", "devto", "npm")

_PROBE_ONLY_EXTRA: dict[str, _Extra] = {
    "keybase": {
        "search_filters": ("keybase.io",),
        "notes": "Public identity directory with a documented lookup API.",
    },
    "hackernews": {
        "search_filters": ("news.ycombinator.com",),
        "notes": "Public user API.",
    },
    "devto": {"search_filters": ("dev.to",), "notes": "Public user API."},
    "npm": {"search_filters": ("npmjs.com",), "notes": "Public registry user API."},
}


def _from_url_platform(entry: SocialPlatform) -> SourceCapability:
    extra: _Extra = _EXTRA.get(entry.key, {})
    probe_key = extra.get("probe_key") or entry.key
    probe: Platform | None = PLATFORMS_BY_KEY.get(probe_key)
    pattern = extra.get("profile_url_pattern")
    return SourceCapability(
        platform=entry.key,
        display_name=entry.display_name,
        domains=tuple(sorted(host.removeprefix("www.") for host in entry.hosts)),
        profile_url_pattern=pattern or None,
        server_fetchable=entry.server_fetchable,
        # A probe endpoint that is not the profile page itself is a documented
        # API. GitHub's api.github.com is one; PyPI's user page is not.
        public_api_available=extra.get(
            "public_api_available", bool(probe and probe.probe_template)
        ),
        # Every recognised platform is worth a manual query: the investigator
        # runs it in their own browser, so a platform blocking *us* is exactly
        # the case where a manual search matters most.
        manual_search_supported=True,
        # A handle check needs a public endpoint we may request without a
        # session. A platform that refuses anonymous clients has none.
        handle_check_supported=bool(probe) and entry.server_fetchable,
        image_reference_supported=extra.get("image_reference_supported", False),
        search_filters=extra.get("search_filters") or (next(iter(sorted(entry.hosts))),),
        notes=extra.get("notes", ""),
        fetch_note=entry.fetch_note,
        probe_key=probe_key if probe else None,
    )


def _from_probe_only(key: str) -> SourceCapability:
    probe = PLATFORMS_BY_KEY[key]
    extra: _Extra = _PROBE_ONLY_EXTRA.get(key, {})
    host = urlsplit(probe.url_template.format(username="x")).hostname or ""
    return SourceCapability(
        platform=key,
        display_name=probe.display_name,
        domains=(host.removeprefix("www."),),
        profile_url_pattern=probe.url_template.replace("{username}", "{handle}"),
        server_fetchable=True,
        public_api_available=bool(probe.probe_template),
        manual_search_supported=True,
        handle_check_supported=True,
        image_reference_supported=False,
        search_filters=extra.get("search_filters") or (host.removeprefix("www."),),
        notes=extra.get("notes", ""),
        probe_key=key,
    )


CAPABILITIES: tuple[SourceCapability, ...] = (
    *(_from_url_platform(entry) for entry in URL_PLATFORMS),
    *(_from_probe_only(key) for key in _PROBE_ONLY),
)

BY_PLATFORM: dict[str, SourceCapability] = {item.platform: item for item in CAPABILITIES}


def capability_for(platform: str) -> SourceCapability | None:
    """What may be done with ``platform``, or ``None`` if it is unrecognised."""
    return BY_PLATFORM.get((platform or "").strip().lower())


def handle_checkable() -> tuple[SourceCapability, ...]:
    """Platforms whose public endpoints can confirm a handle exists."""
    return tuple(item for item in CAPABILITIES if item.handle_check_supported)


def reference_only() -> tuple[SourceCapability, ...]:
    """Platforms recorded from a URL's shape because they refuse automation."""
    return tuple(item for item in CAPABILITIES if not item.server_fetchable)


def searchable() -> tuple[SourceCapability, ...]:
    """Platforms worth a targeted manual query, strongest filter first."""
    return tuple(item for item in CAPABILITIES if item.manual_search_supported)


#: Platforms shown in the person-facing search plan, in the order an
#: investigator works through them. Bounded deliberately: a query list is a
#: worklist, and thirty of them is not one.
SEARCH_PLATFORM_ORDER: tuple[str, ...] = (
    "linkedin",
    "github",
    "orcid",
    "twitter",
    "facebook",
    "instagram",
    "youtube",
    "tiktok",
    "reddit",
    "snapchat",
)


def search_platforms() -> tuple[SourceCapability, ...]:
    """The searchable platforms, in worklist order."""
    ordered = [BY_PLATFORM[key] for key in SEARCH_PLATFORM_ORDER if key in BY_PLATFORM]
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    """The registry as a report or a settings page renders it."""

    directly_checked: tuple[str, ...] = field(default_factory=tuple)
    api_backed: tuple[str, ...] = field(default_factory=tuple)
    reference_only: tuple[str, ...] = field(default_factory=tuple)
    manual_search_only: tuple[str, ...] = field(default_factory=tuple)


def summarise() -> CapabilitySummary:
    """Group platforms by what the platform can actually do with them."""
    return CapabilitySummary(
        directly_checked=tuple(
            item.platform for item in CAPABILITIES if item.handle_check_supported
        ),
        api_backed=tuple(item.platform for item in CAPABILITIES if item.public_api_available),
        reference_only=tuple(item.platform for item in CAPABILITIES if not item.server_fetchable),
        manual_search_only=tuple(
            item.platform
            for item in CAPABILITIES
            if item.manual_search_supported and not item.handle_check_supported
        ),
    )
