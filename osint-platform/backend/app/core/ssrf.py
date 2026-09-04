"""SSRF protection for every outbound request.

The platform fetches URLs that investigators supply, so each one is validated
before a socket is opened and again after every redirect:

* scheme allow-list (``http``/``https`` only);
* no credentials embedded in the URL;
* the hostname is resolved and **every** returned address is checked against
  loopback, private, link-local, reserved, multicast and unspecified ranges,
  plus the well-known cloud metadata endpoints;
* the resolved address is pinned, so a name that resolves differently on the
  second lookup cannot smuggle a request through (DNS rebinding).

``ALLOW_PRIVATE_NETWORKS=true`` disables the private-range checks and exists
only for authorised internal-security engagements.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.errors import SSRFError
from app.core.settings import get_settings

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Cloud instance-metadata services. Blocked regardless of settings because a
#: legitimate OSINT collection never targets them.
METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "100.100.100.100",
    }
)

BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

#: Ranges that are never legitimate collection targets even in "internal" mode.
ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


@dataclass(frozen=True)
class ResolvedTarget:
    """The outcome of validating a URL."""

    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...] = field(default=())


def _is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a human-readable reason if ``addr`` must not be contacted."""
    for network in ALWAYS_BLOCKED_NETWORKS:
        if addr.version == network.version and addr in network:
            return "cloud metadata endpoint"

    if get_settings().allow_private_networks:
        return None

    checks = (
        (addr.is_loopback, "loopback address"),
        (addr.is_private, "private address"),
        (addr.is_link_local, "link-local address"),
        (addr.is_multicast, "multicast address"),
        (addr.is_reserved, "reserved address"),
        (addr.is_unspecified, "unspecified address"),
    )
    for failed, reason in checks:
        if failed:
            return reason
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            nested = _is_blocked_address(mapped)
            if nested:
                return f"IPv4-mapped {nested}"
    return None


def _resolve(hostname: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve host {hostname!r}", detail=str(exc)) from exc
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(str(address))
    if not addresses:
        raise SSRFError(f"Host {hostname!r} resolved to no addresses")
    return addresses


def validate_url(url: str, *, resolve: bool = True) -> ResolvedTarget:
    """Validate ``url`` for outbound fetching.

    Raises :class:`SSRFError` when the URL is malformed, uses a disallowed
    scheme, or resolves to a blocked address.
    """
    if not url or len(url) > 2048:
        raise SSRFError("URL is empty or exceeds the 2048 character limit")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"Scheme {scheme or '(none)'!r} is not allowed")
    if parts.username or parts.password:
        raise SSRFError("Credentials embedded in URLs are not allowed")

    hostname = (parts.hostname or "").lower().rstrip(".")
    if not hostname:
        raise SSRFError("URL has no host")
    if hostname in BLOCKED_HOSTNAMES and not get_settings().allow_private_networks:
        raise SSRFError(f"Host {hostname!r} is blocked (loopback name)")
    if hostname in METADATA_HOSTS:
        raise SSRFError(f"Host {hostname!r} is blocked (cloud metadata endpoint)")

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SSRFError("URL contains an invalid port") from exc
    if not 1 <= port <= 65535:
        raise SSRFError(f"Port {port} is out of range")

    literal = _as_ip(hostname)
    if literal is not None:
        reason = _is_blocked_address(literal)
        if reason:
            raise SSRFError(f"Host {hostname!r} is blocked ({reason})")
        return ResolvedTarget(url, scheme, hostname, port, (str(literal),))

    if not resolve:
        return ResolvedTarget(url, scheme, hostname, port, ())

    addresses = _resolve(hostname, port)
    for address in addresses:
        reason = _is_blocked_address(ipaddress.ip_address(address))
        if reason:
            raise SSRFError(f"Host {hostname!r} resolves to a blocked address ({reason})")
    return ResolvedTarget(url, scheme, hostname, port, tuple(addresses))


def _as_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = hostname.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def is_safe_url(url: str, *, resolve: bool = True) -> bool:
    """Boolean convenience wrapper around :func:`validate_url`."""
    try:
        validate_url(url, resolve=resolve)
    except SSRFError:
        return False
    return True
