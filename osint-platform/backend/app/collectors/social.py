"""Recognising public social and profile URLs.

A URL is classified by *shape* only — host and path — so this module is pure and
exhaustively testable. Classification says "this is a LinkedIn profile URL", and
nothing more. It never says whose profile it is: that judgement belongs to the
correlation rules, which need independent evidence before they will raise a
candidate above a name match.

Only public profile shapes are recognised. Nothing here fetches anything, and
nothing here knows how to reach content behind a login.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit


@dataclass(frozen=True, slots=True)
class SocialPlatform:
    """One recognised public platform and how to read its profile URLs."""

    key: str
    display_name: str
    #: Hosts that belong to this platform, without a ``www.`` prefix.
    hosts: frozenset[str]
    #: Path segments that introduce a handle rather than being one.
    handle_prefixes: frozenset[str] = frozenset()
    #: Path segments that are site furniture, never a profile.
    reserved: frozenset[str] = frozenset()
    #: True when this platform's pages are routinely fetchable by a logged-out
    #: client. False means a server-side fetch is expected to be refused, and
    #: the platform must be reported as unavailable rather than worked around.
    server_fetchable: bool = True
    #: Why a fetch is expected to fail, shown to the investigator.
    fetch_note: str = ""


PLATFORMS: tuple[SocialPlatform, ...] = (
    SocialPlatform(
        key="linkedin",
        display_name="LinkedIn",
        hosts=frozenset({"linkedin.com", "www.linkedin.com"}),
        handle_prefixes=frozenset({"in", "pub", "company", "school"}),
        reserved=frozenset({"feed", "jobs", "learning", "posts", "pulse", "login"}),
        server_fetchable=False,
        fetch_note=(
            "LinkedIn refuses anonymous server-side requests for profile pages. "
            "The URL is recorded as a candidate from its shape; its content is not "
            "fetched, and no attempt is made to work around the block."
        ),
    ),
    SocialPlatform(
        key="instagram",
        display_name="Instagram",
        hosts=frozenset({"instagram.com", "www.instagram.com"}),
        reserved=frozenset({"p", "reel", "reels", "explore", "stories", "accounts", "tv"}),
        server_fetchable=False,
        fetch_note=(
            "Instagram requires an authenticated session for most profile content. "
            "Only the public URL shape is recorded."
        ),
    ),
    SocialPlatform(
        key="facebook",
        display_name="Facebook",
        hosts=frozenset({"facebook.com", "www.facebook.com", "m.facebook.com", "fb.com"}),
        handle_prefixes=frozenset({"profile.php", "people", "pg"}),
        reserved=frozenset({"groups", "events", "watch", "marketplace", "login", "sharer"}),
        server_fetchable=False,
        fetch_note=(
            "Facebook gates profile content behind a login for anonymous clients. "
            "Only the public URL shape is recorded."
        ),
    ),
    SocialPlatform(
        key="youtube",
        display_name="YouTube",
        hosts=frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}),
        handle_prefixes=frozenset({"c", "channel", "user"}),
        reserved=frozenset({"watch", "results", "feed", "playlist", "shorts", "embed"}),
    ),
    SocialPlatform(
        key="snapchat",
        display_name="Snapchat",
        hosts=frozenset({"snapchat.com", "www.snapchat.com"}),
        handle_prefixes=frozenset({"add", "p"}),
        reserved=frozenset({"download", "privacy", "terms", "lens"}),
        server_fetchable=False,
        fetch_note=(
            "Snapchat exposes only a limited public profile surface and refuses most "
            "anonymous server-side requests. Only the public URL shape is recorded."
        ),
    ),
    SocialPlatform(
        key="github",
        display_name="GitHub",
        hosts=frozenset({"github.com", "www.github.com"}),
        reserved=frozenset({"orgs", "topics", "features", "pricing", "search", "login"}),
    ),
    SocialPlatform(
        key="gitlab",
        display_name="GitLab",
        hosts=frozenset({"gitlab.com", "www.gitlab.com"}),
        reserved=frozenset({"explore", "help", "users", "groups"}),
    ),
    SocialPlatform(
        key="orcid",
        display_name="ORCID",
        hosts=frozenset({"orcid.org", "www.orcid.org"}),
    ),
    SocialPlatform(
        key="pypi",
        display_name="PyPI",
        hosts=frozenset({"pypi.org", "www.pypi.org"}),
        handle_prefixes=frozenset({"user"}),
        reserved=frozenset({"project", "search", "help"}),
    ),
    SocialPlatform(
        key="mastodon",
        display_name="Mastodon",
        hosts=frozenset({"mastodon.social", "mastodon.online"}),
    ),
    SocialPlatform(
        key="reddit",
        display_name="Reddit",
        hosts=frozenset({"reddit.com", "www.reddit.com", "old.reddit.com"}),
        handle_prefixes=frozenset({"u", "user"}),
        reserved=frozenset({"r", "comments", "search", "submit"}),
    ),
)

_BY_HOST: dict[str, SocialPlatform] = {
    host: platform for platform in PLATFORMS for host in platform.hosts
}

#: Hosts whose pages are publications rather than profiles.
PUBLICATION_HOSTS = frozenset(
    {
        "doi.org",
        "dx.doi.org",
        "arxiv.org",
        "openalex.org",
        "api.crossref.org",
        "semanticscholar.org",
        "www.semanticscholar.org",
        "researchgate.net",
        "www.researchgate.net",
        "pubmed.ncbi.nlm.nih.gov",
    }
)

_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
#: A leading scheme of any kind, so a non-web one can be refused rather than
#: rewritten into a web address.
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


@dataclass(frozen=True, slots=True)
class SocialProfile:
    """What a URL's shape says about it. Never who it belongs to."""

    #: ``social`` | ``publication`` | ``web``
    kind: str
    platform: str
    display_name: str
    #: The handle in the path, when the shape yields one.
    handle: str | None
    #: Canonical form, used as the candidate's identity.
    url: str
    #: True when a logged-out server fetch is expected to succeed.
    server_fetchable: bool
    fetch_note: str = ""

    @property
    def is_social(self) -> bool:
        return self.kind == "social"


def classify_url(raw: str) -> SocialProfile | None:
    """Classify a public URL by its shape.

    Returns ``None`` for anything that is not http(s). A URL on a recognised
    platform that is *not* profile-shaped (a YouTube watch page, a LinkedIn job
    listing) comes back as ``kind="web"`` on that platform rather than as a
    profile, because treating a video page as a person would invent an identity.
    """
    text = (raw or "").strip()
    if not text:
        return None
    # Only a scheme-less string may be given one. Prepending "https://" to
    # "javascript:alert(1)" would turn a script URI into something that parses
    # as a web address, which is exactly the shape a caller must not be handed.
    scheme_match = _SCHEME_RE.match(text)
    if scheme_match:
        if scheme_match.group(1).lower() not in {"http", "https"}:
            return None
        parts = urlsplit(text)
    else:
        parts = urlsplit(f"https://{text}")
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    platform = _BY_HOST.get(host) or _BY_HOST.get(host.removeprefix("www."))

    if platform is None:
        bare = host.removeprefix("www.")
        if bare in PUBLICATION_HOSTS or host in PUBLICATION_HOSTS:
            return SocialProfile(
                kind="publication",
                platform=bare,
                display_name=bare,
                handle=None,
                url=_canonical(parts.scheme, host, parts.path),
                server_fetchable=True,
            )
        return SocialProfile(
            kind="web",
            platform=bare,
            display_name=bare,
            handle=None,
            url=_canonical(parts.scheme, host, parts.path),
            server_fetchable=True,
        )

    handle = _handle_for(platform, segments, parts.query)
    return SocialProfile(
        kind="social" if handle else "web",
        platform=platform.key,
        display_name=platform.display_name,
        handle=handle,
        url=_canonical(
            parts.scheme, host, parts.path, parts.query if platform.key == "facebook" else ""
        ),
        server_fetchable=platform.server_fetchable,
        fetch_note=platform.fetch_note if not platform.server_fetchable else "",
    )


def _handle_for(platform: SocialPlatform, segments: list[str], query: str) -> str | None:
    """The profile handle a path yields, or ``None`` when it is not a profile."""
    if not segments:
        return None

    first = segments[0].lower()
    if first in platform.reserved:
        return None

    # Facebook's numeric profiles live in a query string.
    if platform.key == "facebook" and first == "profile.php":
        return dict(parse_qsl(query)).get("id") or None

    if first in platform.handle_prefixes:
        return segments[1].lstrip("@") if len(segments) > 1 else None

    # YouTube and Mastodon put the handle first, marked with "@".
    if segments[0].startswith("@"):
        return segments[0].lstrip("@") or None

    if platform.key == "orcid":
        return segments[0] if _ORCID_RE.match(segments[0]) else None

    # A bare first segment is a profile on the platforms that use one.
    return segments[0] or None


def _canonical(scheme: str, host: str, path: str, query: str = "") -> str:
    cleaned = (path or "/").rstrip("/") or "/"
    url = f"{scheme.lower()}://{host.lower()}{cleaned}"
    return f"{url}?{query}" if query else url


def is_supported_social_host(url: str) -> bool:
    """True when the URL is on a platform this module recognises."""
    profile = classify_url(url)
    return profile is not None and profile.platform in {p.key for p in PLATFORMS}


def platform_by_key(key: str) -> SocialPlatform | None:
    for platform in PLATFORMS:
        if platform.key == key:
            return platform
    return None
