"""Target normalisation.

Investigators paste whatever they have: ``@alice``, ``HTTPS://Example.COM.``,
``github.com/octocat/Hello-World``. Everything downstream — deduplication,
collector planning, entity canonicalisation — depends on turning that into one
predictable representation, so normalisation lives here and nowhere else.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import idna

from app.core.errors import ValidationError
from app.models.enums import TargetType

#: Conservative username charset shared by the platforms we check.
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")
REPO_RE = re.compile(r"^([A-Za-z0-9][\w.\-]{0,38})/([\w.\-]{1,100})$")

#: Hosts whose profile URLs we recognise, mapped to their canonical form.
SOCIAL_HOSTS: dict[str, str] = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "reddit.com": "reddit",
    "mastodon.social": "mastodon",
    "news.ycombinator.com": "hackernews",
    "keybase.io": "keybase",
    "medium.com": "medium",
    "dev.to": "devto",
    "stackoverflow.com": "stackoverflow",
    "bitbucket.org": "bitbucket",
}

_SCHEME_PREFIX = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

#: Path segments that introduce a handle rather than being one
#: (``reddit.com/u/spez``, ``mastodon.social/@alice``).
_HANDLE_PREFIXES = frozenset({"u", "user", "users", "profile", "people", "in", "@"})

#: Hosts whose second path segment is still part of the profile, not a repo.
_NON_REPO_HOSTS = frozenset({"reddit.com", "medium.com", "mastodon.social"})

#: ``git@github.com:owner/repo.git`` — an SSH clone URL, not an email address.
_SSH_REPO_RE = re.compile(r"^[\w.\-]+@[\w.\-]+:[\w.\-]+/[\w.\-]+$")


@dataclass(frozen=True, slots=True)
class NormalizedTarget:
    """The canonical form of an investigation target."""

    type: TargetType
    raw_input: str
    value: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.type}:{self.value}"


def normalize_target(raw: str, target_type: TargetType | None = None) -> NormalizedTarget:
    """Normalise ``raw``, detecting its type when not supplied.

    Raises:
        ValidationError: the input is empty, over-long or not valid for its type.
    """
    if raw is None:
        raise ValidationError("Target must not be empty")
    text = raw.strip()
    if not text:
        raise ValidationError("Target must not be empty")
    if len(text) > 1024:
        raise ValidationError("Target exceeds the 1024 character limit")

    resolved = target_type or detect_type(text)
    handler = _HANDLERS.get(resolved)
    if handler is None:  # pragma: no cover - every enum member has a handler
        raise ValidationError(f"Unsupported target type {resolved}")
    return handler(text)


def detect_type(text: str) -> TargetType:
    """Infer the target type from the shape of ``text``."""
    candidate = text.strip()

    if _SCHEME_PREFIX.match(candidate) or _looks_like_web_reference(candidate):
        host = _host_of(candidate)
        parts = _path_parts(candidate)
        if host in SOCIAL_HOSTS and parts:
            if (
                host not in _NON_REPO_HOSTS
                and len(parts) >= 2
                and REPO_RE.match(f"{parts[0]}/{parts[1]}")
            ):
                return TargetType.REPOSITORY
            return TargetType.SOCIAL_PROFILE
        if not _SCHEME_PREFIX.match(candidate) and not parts:
            return TargetType.DOMAIN
        return TargetType.URL

    if candidate.startswith("@"):
        # A leading "@" always means a handle; an invalid one is an error, not
        # something to silently reinterpret as an organisation name.
        return TargetType.USERNAME

    if _SSH_REPO_RE.match(candidate):
        return TargetType.REPOSITORY

    if EMAIL_RE.match(candidate):
        return TargetType.EMAIL

    try:
        ipaddress.ip_address(candidate.strip("[]"))
    except ValueError:
        pass
    else:
        return TargetType.IP

    if REPO_RE.match(candidate) and not _looks_like_domain(candidate.split("/")[0]):
        return TargetType.REPOSITORY

    bare = candidate.rstrip(".")
    if "." in bare and " " not in bare and _looks_like_domain(bare):
        return TargetType.DOMAIN

    if USERNAME_RE.match(candidate):
        return TargetType.USERNAME

    return TargetType.ORGANIZATION


# --------------------------------------------------------------------- helpers


def _looks_like_domain(text: str) -> bool:
    try:
        labels = _to_ascii_domain(text).split(".")
    except ValidationError:
        return False
    return len(labels) >= 2 and all(DOMAIN_LABEL_RE.match(label) for label in labels)


def _to_ascii_domain(text: str) -> str:
    """Lower-case, IDNA-encode and validate a hostname."""
    host = text.strip().strip(".").lower()
    if not host or len(host) > 253:
        raise ValidationError(f"{text!r} is not a valid domain")
    try:
        # `uts46` folds unicode; encoding then decoding yields the ASCII form.
        ascii_host = idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        # Hyphenated or single-label hosts still normalise if they are ASCII.
        if host.isascii() and all(DOMAIN_LABEL_RE.match(p) for p in host.split(".")):
            return host
        raise ValidationError(f"{text!r} is not a valid domain: {exc}") from exc
    return ascii_host


def _with_scheme(text: str) -> str:
    """Add ``https://`` to a scheme-less ``host/path`` input."""
    return text if _SCHEME_PREFIX.match(text) else f"https://{text}"


def _path_parts(url: str) -> list[str]:
    return [part for part in urlsplit(_with_scheme(url)).path.split("/") if part]


def _host_of(url: str) -> str:
    return (urlsplit(_with_scheme(url)).hostname or "").lower().removeprefix("www.")


def _looks_like_web_reference(text: str) -> bool:
    """True for ``example.com/path`` style input that has no scheme."""
    if _SCHEME_PREFIX.match(text) or "/" not in text or " " in text:
        return False
    return _looks_like_domain(text.split("/", 1)[0])


def _handle_from_path(parts: list[str]) -> str | None:
    """Pick the handle out of a profile path, skipping routing segments."""
    for part in parts:
        cleaned = part.lstrip("@")
        if part.lower() in _HANDLE_PREFIXES or not cleaned:
            continue
        return cleaned
    return None


# -------------------------------------------------------------------- handlers


def _normalize_username(text: str) -> NormalizedTarget:
    value = text.strip().lstrip("@")
    if not USERNAME_RE.match(value):
        raise ValidationError(
            f"{text!r} is not a valid username (letters, digits, '.', '_' and '-', max 64)"
        )
    return NormalizedTarget(TargetType.USERNAME, text, value.lower(), {"case_sensitive": value})


def _normalize_domain(text: str) -> NormalizedTarget:
    candidate = text.strip()
    if _SCHEME_PREFIX.match(candidate):
        candidate = urlsplit(candidate).hostname or ""
    candidate = candidate.split("/")[0].split("@")[-1]
    if ":" in candidate and not candidate.startswith("["):
        candidate = candidate.split(":")[0]
    ascii_host = _to_ascii_domain(candidate)
    labels = ascii_host.split(".")
    if len(labels) < 2 or not all(DOMAIN_LABEL_RE.match(label) for label in labels):
        raise ValidationError(f"{text!r} is not a valid domain")
    attributes: dict[str, Any] = {"labels": labels, "registrable_hint": ".".join(labels[-2:])}
    if ascii_host != candidate.lower().strip("."):
        attributes["unicode"] = candidate.lower().strip(".")
    return NormalizedTarget(TargetType.DOMAIN, text, ascii_host, attributes)


def _normalize_email(text: str) -> NormalizedTarget:
    candidate = text.strip().strip("<>")
    if not EMAIL_RE.match(candidate):
        raise ValidationError(f"{text!r} is not a valid email address")
    local, _, domain = candidate.rpartition("@")
    if not local:
        raise ValidationError(f"{text!r} is not a valid email address")
    ascii_domain = _to_ascii_domain(domain)
    if len(ascii_domain.split(".")) < 2:
        raise ValidationError(f"{text!r} does not contain a valid domain")
    value = f"{local.lower()}@{ascii_domain}"
    return NormalizedTarget(
        TargetType.EMAIL,
        text,
        value,
        {"local_part": local.lower(), "domain": ascii_domain},
    )


def _normalize_url(text: str) -> NormalizedTarget:
    candidate = text.strip()
    if not _SCHEME_PREFIX.match(candidate):
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValidationError(f"URL scheme {scheme!r} is not supported (use http or https)")
    if not parts.hostname:
        raise ValidationError(f"{text!r} has no host")
    host = _to_ascii_domain(parts.hostname) if not _is_ip(parts.hostname) else parts.hostname
    try:
        port = parts.port
    except ValueError as exc:
        raise ValidationError(f"{text!r} has an invalid port") from exc
    netloc = host
    if port and port not in {80, 443}:
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    value = urlunsplit((scheme, netloc, path, parts.query, ""))
    return NormalizedTarget(
        TargetType.URL,
        text,
        value,
        {"scheme": scheme, "host": host, "path": path, "query": parts.query},
    )


def _normalize_ip(text: str) -> NormalizedTarget:
    candidate = text.strip().strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise ValidationError(f"{text!r} is not a valid IP address") from exc
    return NormalizedTarget(
        TargetType.IP,
        text,
        str(address),
        {
            "version": address.version,
            "is_private": address.is_private,
            "is_global": address.is_global,
        },
    )


def _normalize_repository(text: str) -> NormalizedTarget:
    candidate = text.strip()
    host = "github.com"
    if (
        _SCHEME_PREFIX.match(candidate)
        or candidate.startswith("git@")
        or _looks_like_web_reference(candidate)
    ):
        if candidate.startswith("git@"):
            host, _, path = candidate[4:].partition(":")
            parts = [p for p in path.split("/") if p]
        else:
            split = urlsplit(_with_scheme(candidate))
            host = (split.hostname or host).lower().removeprefix("www.")
            parts = _path_parts(candidate)
        if len(parts) < 2:
            raise ValidationError(f"{text!r} does not name a repository (expected owner/name)")
        owner, name = parts[0], parts[1]
    else:
        match = REPO_RE.match(candidate)
        if not match:
            raise ValidationError(f"{text!r} is not a repository reference (expected owner/name)")
        owner, name = match.group(1), match.group(2)

    name = name.removesuffix(".git")
    platform = SOCIAL_HOSTS.get(host, host)
    value = f"{platform}:{owner.lower()}/{name.lower()}"
    return NormalizedTarget(
        TargetType.REPOSITORY,
        text,
        value,
        {
            "platform": platform,
            "host": host,
            "owner": owner,
            "name": name,
            "url": f"https://{host}/{owner}/{name}",
        },
    )


def _normalize_social_profile(text: str) -> NormalizedTarget:
    candidate = text.strip()
    if not _SCHEME_PREFIX.match(candidate):
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path_parts = _path_parts(candidate)
    if not host or not path_parts:
        raise ValidationError(f"{text!r} is not a public profile URL")
    platform = SOCIAL_HOSTS.get(host, host)
    handle = _handle_from_path(path_parts)
    if handle is None:
        # Hacker News carries the handle in the query string (``/user?id=pg``).
        handle = dict(parse_qsl(parts.query)).get("id", "")
    if not handle:
        raise ValidationError(f"{text!r} does not identify a profile handle")
    value = f"{platform}:{handle.lower()}"
    return NormalizedTarget(
        TargetType.SOCIAL_PROFILE,
        text,
        value,
        {
            "platform": platform,
            "handle": handle,
            "url": urlunsplit((parts.scheme.lower(), host, parts.path, "", "")),
        },
    )


def _normalize_organization(text: str) -> NormalizedTarget:
    collapsed = re.sub(r"\s+", " ", text.strip())
    if len(collapsed) < 2:
        raise ValidationError("Organisation names must be at least 2 characters")
    #: Canonical form drops punctuation and common suffixes so that
    #: "Example Corp." and "Example Corporation" collide predictably.
    simplified = re.sub(r"[^a-z0-9 ]+", "", collapsed.lower()).strip()
    simplified = re.sub(
        r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|gmbh|plc|sa|bv|ag|co)\b",
        "",
        simplified,
    )
    simplified = re.sub(r"\s+", " ", simplified).strip() or collapsed.lower()
    return NormalizedTarget(TargetType.ORGANIZATION, text, simplified, {"display_name": collapsed})


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


_HANDLERS = {
    TargetType.USERNAME: _normalize_username,
    TargetType.DOMAIN: _normalize_domain,
    TargetType.EMAIL: _normalize_email,
    TargetType.URL: _normalize_url,
    TargetType.IP: _normalize_ip,
    TargetType.REPOSITORY: _normalize_repository,
    TargetType.SOCIAL_PROFILE: _normalize_social_profile,
    TargetType.ORGANIZATION: _normalize_organization,
}


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (``a.b.example.co.uk`` → ``example.co.uk``).

    Uses a small suffix heuristic rather than a bundled public-suffix list; the
    result is only ever used as a grouping hint, never as an assertion.
    """
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    two_part_suffixes = {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "co.jp",
        "co.in",
        "co.za",
        "com.br",
        "com.mx",
        "com.cn",
    }
    if ".".join(labels[-2:]) in two_part_suffixes:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
