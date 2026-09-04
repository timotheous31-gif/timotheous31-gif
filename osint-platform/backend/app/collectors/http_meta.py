"""HTTP metadata collector.

Fetches the *public* front page of a site once and records what a browser would
already see: status, title, description, canonical link, redirect chain, server
and security headers, and whether robots.txt / sitemap.xml exist.

Politeness is structural: one page, no crawling, no link following beyond the
redirect chain, robots.txt honoured by default, and a short timeout.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from selectolax.parser import HTMLParser

from app.collectors.base import (
    BaseCollector,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import register_collector
from app.core import http
from app.core.errors import PolicyError
from app.core.logging import get_logger
from app.core.ratelimit import RateLimit
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

#: Headers worth reporting. Everything else is dropped rather than stored.
INTERESTING_HEADERS = (
    "server",
    "x-powered-by",
    "content-type",
    "location",
    "via",
    "x-generator",
    "cf-ray",
)

#: Security headers scored in the report. Missing ones are reported as missing.
SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
)

MAX_HTML_BYTES = 1_500_000


@register_collector
class HTTPMetadataCollector(BaseCollector):
    """Collect public metadata from a website's front page."""

    name = "http_meta"
    version = "1.0.0"
    description = "Public page metadata: title, headers, redirects, security headers, robots."
    supported_targets = [TargetType.DOMAIN, TargetType.URL]
    requires_api_key = False
    rate_limit = RateLimit(requests=2, per_seconds=1.0, concurrency=2)
    timeout = 15.0
    default_confidence = 0.9
    source_attribution = "Direct HTTP request to the public website"

    def _url(self, target: NormalizedTarget) -> str:
        if target.type is TargetType.URL:
            return target.value
        return f"https://{target.value}/"

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        url = self._url(target)
        result = CollectorResult(stats={"url": url})

        if self.settings.respect_robots_txt:
            allowed, robots_note = await self._robots_allows(url)
            result.stats["robots_txt"] = robots_note
            if not allowed:
                raise PolicyError(f"robots.txt at {urlsplit(url).netloc} disallows fetching {url}")

        response = await http.get(
            url,
            provider=self.name,
            timeout=self.timeout,
            retry=self.retry,
            max_bytes=MAX_HTML_BYTES,
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        )
        result.stats["status"] = response.status_code
        result.stats["redirects"] = len(response.redirects)

        payload = RawPayload(
            source_url=url,
            content={
                "url": url,
                "final_url": response.final_url,
                "status_code": response.status_code,
                "headers": response.headers,
                "redirects": response.redirects,
                "html": response.text[:MAX_HTML_BYTES] if _is_html(response) else "",
                "elapsed_ms": response.elapsed_ms,
            },
            content_type=response.header("content-type", "text/html") or "text/html",
            status_code=response.status_code,
            retrieved_at=datetime.now(UTC),
        )
        for draft in self.normalize(payload, target):
            result.add(draft, payload)

        for name, exists, probe_url in await self._probe_well_known(response.final_url):
            result.add(
                FindingDraft(
                    kind=FindingKind.HTTP_METADATA,
                    title=f"{name} {'present' if exists else 'absent'} on {urlsplit(url).netloc}",
                    summary=f"{probe_url} returned {'200' if exists else 'no content'}",
                    data={"file": name, "exists": exists, "url": probe_url},
                    source_url=probe_url,
                    confidence=0.9,
                    confidence_reasons=[f"Direct request to {probe_url}"],
                    classification=Classification.PUBLIC,
                    dedupe_key=f"http:{urlsplit(url).netloc}:{name}",
                )
            )
        return result

    async def _robots_allows(self, url: str) -> tuple[bool, str]:
        """Check robots.txt for our user agent. Unreachable robots means allowed."""
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            response = await http.get(
                robots_url,
                provider=self.name,
                timeout=min(self.timeout, 8.0),
                max_bytes=512_000,
                cache_ttl=self.settings.cache_ttl_seconds,
            )
        except Exception as exc:
            log.info("http_meta.robots_unreachable", url=robots_url, error_type=type(exc).__name__)
            return True, f"unreachable ({type(exc).__name__})"
        if response.status_code >= 400:
            return True, f"absent (HTTP {response.status_code})"

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        agent = self.settings.http_user_agent.split("/")[0]
        allowed = parser.can_fetch(agent, url) and parser.can_fetch("*", url)
        return allowed, "present"

    async def _probe_well_known(self, base_url: str) -> list[tuple[str, bool, str]]:
        """HEAD robots.txt and sitemap.xml — presence only, contents not stored."""
        results: list[tuple[str, bool, str]] = []
        for name in ("robots.txt", "sitemap.xml"):
            probe_url = urljoin(base_url, f"/{name}")
            try:
                response = await http.head(
                    probe_url, provider=self.name, timeout=min(self.timeout, 8.0)
                )
                exists = response.status_code < 400
            except Exception as exc:
                log.info("http_meta.probe_failed", url=probe_url, error_type=type(exc).__name__)
                continue
            results.append((name, exists, probe_url))
        return results

    def normalize(self, raw: RawPayload, target: NormalizedTarget) -> list[FindingDraft]:
        content: dict[str, Any] = raw.content
        headers: dict[str, str] = content.get("headers", {})
        html: str = content.get("html", "")
        final_url: str = content.get("final_url", content["url"])
        host = urlsplit(final_url).netloc

        meta = _parse_html(html) if html else {}
        present_security = {name: headers[name] for name in SECURITY_HEADERS if name in headers}
        missing_security = [name for name in SECURITY_HEADERS if name not in headers]

        data: dict[str, Any] = {
            "url": content["url"],
            "final_url": final_url,
            "status_code": content["status_code"],
            "redirect_chain": content.get("redirects", []),
            "server_headers": {
                name: headers[name] for name in INTERESTING_HEADERS if name in headers
            },
            "https": final_url.startswith("https://"),
            "elapsed_ms": content.get("elapsed_ms"),
            **meta,
        }

        findings = [
            FindingDraft(
                kind=FindingKind.HTTP_METADATA,
                title=meta.get("title") or f"HTTP metadata for {host}",
                summary=(
                    f"HTTP {content['status_code']} from {final_url}"
                    + (
                        f" after {len(content.get('redirects', []))} redirect(s)"
                        if content.get("redirects")
                        else ""
                    )
                ),
                data=data,
                source_url=content["url"],
                confidence=self.default_confidence,
                confidence_reasons=["Observed directly from the site's own HTTP response"],
                classification=Classification.PUBLIC,
                dedupe_key=f"http:{host}:metadata",
            ),
            FindingDraft(
                kind=FindingKind.SECURITY_HEADER,
                title=f"Security headers on {host}",
                summary=(
                    f"{len(present_security)} of {len(SECURITY_HEADERS)} common security "
                    f"headers present"
                ),
                data={
                    "present": present_security,
                    "missing": missing_security,
                    "score": round(len(present_security) / len(SECURITY_HEADERS), 2),
                    "url": final_url,
                },
                source_url=content["url"],
                confidence=self.default_confidence,
                confidence_reasons=["Read from the response headers of the site itself"],
                classification=Classification.PUBLIC,
                dedupe_key=f"http:{host}:security-headers",
            ),
        ]

        for link in meta.get("outbound_profile_links", []):
            findings.append(
                FindingDraft(
                    kind=FindingKind.HTTP_METADATA,
                    title=f"{host} links to {link}",
                    summary="The site publishes a link to this profile",
                    data={"from": final_url, "to": link, "relation": "site_links_to_profile"},
                    source_url=content["url"],
                    # A site linking to a profile is a strong, self-published
                    # association — the site owner asserts it themselves.
                    confidence=0.9,
                    confidence_reasons=[
                        "The website itself publishes a link to this profile",
                    ],
                    classification=Classification.PUBLIC,
                    dedupe_key=f"http:{host}:link:{link}",
                )
            )
        return findings


def _is_html(response: http.HttpResponse) -> bool:
    content_type = (response.header("content-type") or "").lower()
    return "html" in content_type or "xml" in content_type


#: Hosts whose links from a homepage are meaningful identity signals.
PROFILE_HOSTS = (
    "github.com",
    "gitlab.com",
    "mastodon.social",
    "reddit.com",
    "keybase.io",
    "medium.com",
    "dev.to",
    "stackoverflow.com",
    "bitbucket.org",
)


def _parse_html(html: str) -> dict[str, Any]:
    """Extract the metadata a browser would show, plus self-published links."""
    tree = HTMLParser(html)
    meta: dict[str, Any] = {}

    title_node = tree.css_first("title")
    if title_node and title_node.text(strip=True):
        meta["title"] = title_node.text(strip=True)[:300]

    for node in tree.css("meta"):
        name = (node.attributes.get("name") or node.attributes.get("property") or "").lower()
        value = (node.attributes.get("content") or "").strip()
        if not name or not value:
            continue
        if name in {"description", "og:description"} and "description" not in meta:
            meta["description"] = value[:500]
        elif name == "generator":
            meta["generator"] = value[:200]
        elif name in {"og:site_name"}:
            meta["site_name"] = value[:200]

    canonical = tree.css_first('link[rel="canonical"]')
    if canonical and canonical.attributes.get("href"):
        meta["canonical_url"] = str(canonical.attributes["href"])[:2048]

    links: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = str(anchor.attributes.get("href") or "")
        if not href.startswith(("http://", "https://")):
            continue
        host = (urlsplit(href).hostname or "").lower().removeprefix("www.")
        if host in PROFILE_HOSTS:
            links.add(href.split("?")[0][:500])
    if links:
        meta["outbound_profile_links"] = sorted(links)[:25]

    return meta
