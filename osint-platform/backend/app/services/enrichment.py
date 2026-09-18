"""Bounded public-text enrichment for search results that carry no description.

Some search providers return a URL and a title and nothing else. Anthropic's web
search is one: a result is ``url``, ``title``, ``page_age`` and an opaque blob,
and there is no snippet field at all. That is a real problem for this platform,
because the correlation engine reads a result's public text to see whether a
supplied employer or a supplied city actually appears on the page. With no text,
every result correlates on the displayed name alone.

There are two honest responses to that, and exactly one dishonest one.

The dishonest one is to invent a description — from the URL, from the title, from
a model's summary of the page. That would put words into a finding that no public
source published, and the whole evidence model rests on not doing that.

The honest ones are to accept the result as a low-context discovery candidate, or
to go and read a small number of the pages. This module is the second, kept
narrow on purpose:

* **It is not a crawler.** One request per page, no link following, no second
  page, and a hard ceiling on how many pages one investigation may fetch.
* **It only fetches what is worth fetching.** The caller chooses; this module
  refuses anything the platform already knows it should not fetch.
* **It uses the one guarded client.** ``app.core.http`` and nothing else, so the
  SSRF guard, the redirect validation, the byte ceiling, the timeout and the rate
  limiter all apply exactly as they do to every collector. A search result is a
  third party's description of a page neither of us controls, and a malicious
  entry in a result set is an ordinary thing to expect.
* **robots.txt is honoured** when the deployment asks for it, the same as the
  HTTP metadata collector.
* **It keeps an excerpt, not a copy.** A few hundred characters of the visible
  text, so a human can see what the page says about the anchors — not a mirror of
  someone else's page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from selectolax.parser import HTMLParser

from app.core import http
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

log = get_logger(__name__)

#: Rate-limit bucket and log label. Separate from any collector's, so enrichment
#: traffic is visible as its own thing.
ENRICHMENT_PROVIDER = "result_enrichment"

#: Elements whose text is chrome rather than content.
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "nav", "footer", "header")

#: Outcomes. Each is a different fact and the report needs to be able to tell
#: them apart — above all, "we chose not to fetch" from "we fetched and the page
#: said nothing".
FETCHED = "fetched"
SKIPPED_DISABLED = "skipped_disabled"
SKIPPED_BUDGET = "skipped_budget"
SKIPPED_POLICY = "skipped_policy"
SKIPPED_NOT_FETCHABLE = "skipped_not_fetchable"
SKIPPED_NOT_HTML = "skipped_not_html"
REJECTED_URL = "rejected_url"
FAILED = "failed"


@dataclass(slots=True)
class PageExcerpt:
    """What one enrichment attempt established, including having not tried."""

    url: str
    state: str
    excerpt: str = ""
    title: str = ""
    status_code: int | None = None
    robots: str = ""
    detail: str = ""
    fetched_at: datetime | None = None

    @property
    def has_text(self) -> bool:
        return self.state == FETCHED and bool(self.excerpt)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state,
            "excerpt": self.excerpt or None,
            "page_title": self.title or None,
            "status_code": self.status_code,
            "robots": self.robots or None,
            "detail": self.detail or None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "source": "direct HTTP request to the public page, through the "
            "platform's SSRF-guarded client",
        }


@dataclass(slots=True)
class EnrichmentBudget:
    """How many pages one investigation may read, and what it spent.

    Counts every *decision*, not just every fetch, so a report can say how many
    candidates were considered and why the ones that were skipped were skipped.
    """

    limit: int
    used: int = 0
    considered: int = 0
    outcomes: list[PageExcerpt] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def record(self, excerpt: PageExcerpt) -> PageExcerpt:
        self.considered += 1
        if excerpt.state == FETCHED:
            self.used += 1
        self.outcomes.append(excerpt)
        return excerpt

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "fetches_used": self.used,
            "candidates_considered": self.considered,
            "outcomes": [item.to_dict() for item in self.outcomes],
        }


async def robots_allows(
    url: str,
    *,
    provider: str,
    settings: Settings | None = None,
    timeout: float = 8.0,  # noqa: ASYNC109 - a per-request budget, not a cancel scope
) -> tuple[bool, str]:
    """Whether robots.txt permits fetching ``url`` for our user agent.

    Shared with the HTTP metadata collector rather than written twice, because two
    implementations of a politeness rule is how one of them ends up wrong. An
    unreachable or absent robots.txt means allowed, which is what the standard
    says and what every well-behaved client does.
    """
    settings = settings or get_settings()
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = await http.get(
            robots_url,
            provider=provider,
            timeout=timeout,
            max_bytes=512_000,
            cache_ttl=settings.cache_ttl_seconds,
        )
    except Exception as exc:
        log.info("robots.unreachable", url=robots_url, error_type=type(exc).__name__)
        return True, f"unreachable ({type(exc).__name__})"
    if response.status_code >= 400:
        return True, f"absent (HTTP {response.status_code})"

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    agent = settings.http_user_agent.split("/")[0]
    allowed = parser.can_fetch(agent, url) and parser.can_fetch("*", url)
    return allowed, "present"


async def fetch_excerpt(
    url: str,
    *,
    budget: EnrichmentBudget,
    server_fetchable: bool = True,
    fetch_note: str = "",
    settings: Settings | None = None,
) -> PageExcerpt:
    """Read a short public text excerpt from one page, if that is permitted.

    Every refusal is recorded with its reason. A platform that blocks server-side
    fetching is ``skipped_not_fetchable`` and never "the page does not exist" —
    interpreting an HTTP block as an absence is the error this whole coverage
    vocabulary exists to prevent.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)

    if not settings.result_enrichment_enabled:
        return budget.record(
            PageExcerpt(
                url=url,
                state=SKIPPED_DISABLED,
                detail="Result enrichment is switched off for this deployment.",
            )
        )
    if budget.exhausted:
        return budget.record(
            PageExcerpt(
                url=url,
                state=SKIPPED_BUDGET,
                detail=(
                    f"The per-investigation enrichment budget of {budget.limit} page(s) "
                    f"was already spent."
                ),
            )
        )
    if not server_fetchable:
        return budget.record(
            PageExcerpt(
                url=url,
                state=SKIPPED_NOT_FETCHABLE,
                detail=fetch_note
                or "This platform blocks server-side fetching of its public pages.",
            )
        )

    if settings.respect_robots_txt:
        allowed, note = await robots_allows(url, provider=ENRICHMENT_PROVIDER, settings=settings)
        if not allowed:
            return budget.record(
                PageExcerpt(
                    url=url,
                    state=SKIPPED_POLICY,
                    robots=note,
                    detail="robots.txt disallows fetching this page.",
                )
            )
    else:
        note = "not checked"

    try:
        response = await http.get(
            url,
            provider=ENRICHMENT_PROVIDER,
            max_bytes=settings.result_enrichment_max_bytes,
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        )
    except Exception as exc:
        log.info("enrichment.fetch_failed", error_type=type(exc).__name__)
        return budget.record(
            PageExcerpt(
                url=url,
                state=FAILED,
                robots=note,
                detail=f"{type(exc).__name__} while fetching the page.",
                fetched_at=now,
            )
        )

    content_type = (response.header("content-type") or "").lower()
    if "html" not in content_type and "xml" not in content_type:
        return budget.record(
            PageExcerpt(
                url=url,
                state=SKIPPED_NOT_HTML,
                status_code=response.status_code,
                robots=note,
                detail=f"The page served {content_type or 'an unstated content type'}.",
                fetched_at=now,
            )
        )
    if not response.ok:
        return budget.record(
            PageExcerpt(
                url=url,
                state=FAILED,
                status_code=response.status_code,
                robots=note,
                detail=f"The page returned HTTP {response.status_code}.",
                fetched_at=now,
            )
        )

    title, text = _visible_text(response.text)
    return budget.record(
        PageExcerpt(
            url=url,
            state=FETCHED,
            excerpt=text[: settings.result_enrichment_excerpt_chars],
            title=title,
            status_code=response.status_code,
            robots=note,
            fetched_at=now,
        )
    )


def _visible_text(html: str) -> tuple[str, str]:
    """The page's title and its visible body text, collapsed to one line.

    Nothing is interpreted here. The text is kept as the page published it so a
    human reading the finding sees the page's own words, and so the anchor checks
    run against real public text rather than against a paraphrase.
    """
    if not html:
        return "", ""
    tree = HTMLParser(html)
    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    title_node = tree.css_first("title")
    title = " ".join((title_node.text() if title_node else "").split())[:300]
    body = tree.body or tree.root
    text = " ".join((body.text(separator=" ") if body else "").split())
    return title, text
