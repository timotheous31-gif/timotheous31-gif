"""Reconnaissance query generation for PERSON targets.

The platform does not scrape search engines. It generates the queries an
investigator would type, hands them over to be opened in a normal browser, and
accepts back whatever public results the investigator chose to keep. This module
is the first half of that: a pure, deterministic function from a name plus
anchors to a bounded, deduplicated, explained list of queries.

Two rules shape what may be generated:

* **Only public-surface queries.** Query families are declared here explicitly.
  There is no free-form template, so "<name> home address" cannot be produced by
  a caller passing an unexpected anchor — the generator has no branch that could
  emit it. :data:`FORBIDDEN_TERMS` then re-checks the output, so the rule holds
  even if a future family is added carelessly.
* **Every query explains itself.** A query an investigator cannot justify is one
  they should not run, so each carries the family it belongs to, the anchors it
  used, and a plain-language rationale.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.collectors.person import PersonContext, normalize_handle

#: Query families, ordered by how much signal they usually carry.
FAMILY_GENERAL = "general"
FAMILY_SOCIAL = "social"
FAMILY_ACADEMIC = "academic"
FAMILY_DOCUMENT = "document"
FAMILY_ANCHOR = "anchor"
FAMILY_IMAGE = "image"

#: Public platforms worth a targeted query, as (label, site: filter).
SOCIAL_SITES: tuple[tuple[str, str], ...] = (
    ("LinkedIn", "linkedin.com"),
    ("Instagram", "instagram.com"),
    ("Facebook", "facebook.com"),
    ("YouTube", "youtube.com"),
    ("X (Twitter)", "x.com"),
    ("TikTok", "tiktok.com"),
    ("Snapchat", "snapchat.com"),
    ("GitHub", "github.com"),
    ("ORCID", "orcid.org"),
)

#: Words that bring public photographs of a named person to the surface. These
#: search *pages that publish a picture*, which is what image evidence means
#: here — never a reverse image lookup, and never anything that identifies a
#: person from a photograph.
IMAGE_TERMS: tuple[str, ...] = ("photo", "image", "profile picture", "headshot")

#: Terms that must never appear in a generated query. This is a belt-and-braces
#: check over the output of families that are already narrow by construction:
#: the point is that adding a careless family later fails a test rather than
#: quietly shipping an invasive query.
#: Terms whose only purpose in a query about a person is to extract a private
#: fact. Refused wherever they appear — including inside an anchor, so that the
#: anchor fields cannot be used to smuggle a query past this screen.
NEVER_SEARCHABLE: frozenset[str] = frozenset(
    {
        "address",
        "home address",
        "phone",
        "phone number",
        "mobile number",
        "password",
        "passwords",
        "credentials",
        "leak",
        "leaked",
        "breach",
        "dob",
        "date of birth",
        "birthday",
        "ssn",
        "social security",
        "passport",
        "driver licence",
        "driver license",
        "national id",
        "wife",
        "husband",
        "spouse",
        "girlfriend",
        "boyfriend",
        "children",
        "kids",
        "relatives",
        "neighbour",
        "neighbor",
        "salary",
        "arrest",
        "criminal record",
        "mugshot",
        "sexuality",
        "location now",
        "live location",
        "gps",
        "coordinates",
    }
)

#: Words that name a category of sensitive information *and* routinely appear in
#: the names of real institutions. "Liaquat University of Medical & Health
#: Sciences" is an employer; ``"A Name" medical`` is fishing for health data.
#: The difference is not in the word, it is in who put it there — so these are
#: refused when the *platform* adds them and permitted inside a value the
#: investigator supplied as an anchor.
#:
#: The split is narrow on purpose. Everything in NEVER_SEARCHABLE stays refused
#: in an anchor too, which is what stops the anchor fields becoming a way around
#: this screen.
CONTEXTUAL_TERMS: frozenset[str] = frozenset(
    {
        "medical",
        "health",
        "diagnosis",
        "religion",
        "family",
    }
)

#: Everything screened when the platform composes a query on its own.
FORBIDDEN_TERMS: frozenset[str] = NEVER_SEARCHABLE | CONTEXTUAL_TERMS

#: Ceiling on generated queries. A recon list is something a human works
#: through, so it stays human-sized.
MAX_QUERIES = 40


@dataclass(frozen=True, slots=True)
class ReconQuery:
    """One search a human is invited to run, and why."""

    #: The query text, ready to paste into a search engine.
    query: str
    family: str
    #: Why this query is worth running, in plain language.
    rationale: str
    #: Lower sorts first. Anchored queries outrank unanchored ones because they
    #: return fewer strangers.
    priority: int
    #: Which anchors the query was built from, e.g. ``["organization"]``.
    anchors_used: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Identity for deduplication: the query text, case-folded."""
        return " ".join(self.query.lower().split())

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "family": self.family,
            "rationale": self.rationale,
            "priority": self.priority,
            "anchors_used": list(self.anchors_used),
        }


def _quoted(name: str) -> str:
    return f'"{name}"'


def generate_queries(name: str, context: PersonContext | None = None) -> list[ReconQuery]:
    """Build the recon query list for ``name`` and its anchors.

    Deterministic: the same inputs always produce the same list in the same
    order, so a case can be re-opened and the investigator sees what they saw
    before. Duplicates are collapsed on the query text, keeping the first
    (highest-priority) occurrence and its rationale.
    """
    display = " ".join((name or "").split())
    if not display:
        return []

    context = context or PersonContext()
    quoted = _quoted(display)
    queries: list[ReconQuery] = [
        ReconQuery(
            query=quoted,
            family=FAMILY_GENERAL,
            rationale="The plainest search: every public page that names them.",
            priority=10,
        )
    ]
    queries.extend(_social_queries(quoted, display))
    queries.extend(_image_queries(quoted, context))
    queries.extend(_academic_queries(quoted))
    queries.extend(_anchor_queries(quoted, context))

    return _finalise(queries, context)


def _image_queries(quoted: str, context: PersonContext) -> list[ReconQuery]:
    """Queries that surface pages publishing a public photograph.

    These find *pages*, which is the only thing image evidence claims: a picture
    appearing somewhere that names the subject. Nothing here searches by an
    image, and nothing identifies a person from one.
    """
    out: list[ReconQuery] = []
    for term in IMAGE_TERMS:
        out.append(
            ReconQuery(
                query=f"{quoted} {term}",
                family=FAMILY_IMAGE,
                rationale=(
                    f"Public pages that publish a {term} alongside the name. The image "
                    f"is context for the page, not an identification."
                ),
                priority=45,
            )
        )
    # An affiliation narrows an image search far more than any other anchor: a
    # conference or faculty page names people beside their photographs.
    for affiliation in context.affiliations[:2]:
        out.append(
            ReconQuery(
                query=f'{quoted} "{affiliation}" photo',
                family=FAMILY_IMAGE,
                rationale=(
                    f"Pages publishing a photograph in the context of {affiliation}, "
                    f"such as staff, faculty or conference listings."
                ),
                priority=15,
                anchors_used=["affiliation"],
            )
        )
    return out


def _social_queries(quoted: str, display: str) -> list[ReconQuery]:
    out: list[ReconQuery] = []
    for label, site in SOCIAL_SITES:
        out.append(
            ReconQuery(
                query=f"{quoted} {label}",
                family=FAMILY_SOCIAL,
                rationale=(
                    f"Pages that mention the name alongside {label}, including "
                    f"third-party pages that link to a {label} profile."
                ),
                priority=40,
            )
        )
        out.append(
            ReconQuery(
                query=f"{quoted} site:{site}",
                family=FAMILY_SOCIAL,
                rationale=f"Public {label} pages only, which usually surfaces profiles directly.",
                priority=30,
            )
        )
    out.append(
        ReconQuery(
            query=f"{quoted} profile",
            family=FAMILY_SOCIAL,
            rationale="Profile-style pages on sites outside the major platforms.",
            priority=50,
        )
    )
    return out


def _academic_queries(quoted: str) -> list[ReconQuery]:
    return [
        ReconQuery(
            query=f"{quoted} research",
            family=FAMILY_ACADEMIC,
            rationale="Research pages, group listings and staff directories.",
            priority=35,
        ),
        ReconQuery(
            query=f"{quoted} publication",
            family=FAMILY_ACADEMIC,
            rationale="Publication lists and citations, which carry affiliations.",
            priority=35,
        ),
        ReconQuery(
            query=f"{quoted} site:edu",
            family=FAMILY_ACADEMIC,
            rationale="University pages: staff, students, conferences and theses.",
            priority=35,
        ),
        ReconQuery(
            query=f"{quoted} filetype:pdf",
            family=FAMILY_DOCUMENT,
            rationale=(
                "PDFs — papers, programmes, reports and minutes — often name people "
                "with their affiliation attached."
            ),
            priority=45,
        ),
    ]


def _anchor_queries(quoted: str, context: PersonContext) -> list[ReconQuery]:
    """Queries that pair the name with something the investigator already knows.

    These are the highest-value queries, because an anchor is what turns "every
    person with this name" into "this person". Each anchor is quoted so the
    engine treats it as a phrase.
    """
    out: list[ReconQuery] = []

    for organization in context.organizations:
        out.append(
            ReconQuery(
                query=f'{quoted} "{organization}"',
                family=FAMILY_ANCHOR,
                rationale=(
                    f"Pages naming them together with {organization}, the organisation "
                    f"you supplied — far fewer same-name strangers than the name alone."
                ),
                priority=5,
                anchors_used=["organization"],
            )
        )
    for school in context.schools:
        out.append(
            ReconQuery(
                query=f'{quoted} "{school}"',
                family=FAMILY_ANCHOR,
                rationale=f"Pages naming them together with {school}, the school you supplied.",
                priority=5,
                anchors_used=["school"],
            )
        )
    # Normalise here rather than trusting the constructor: a context built
    # directly in code has not been through ``from_target``, and "OCTOCAT",
    # " octocat " and "octocat" must produce one query, not three.
    for handle in dict.fromkeys(
        normalized for raw in context.all_handles if (normalized := normalize_handle(raw))
    ):
        out.append(
            ReconQuery(
                query=f'{quoted} "{handle}"',
                family=FAMILY_ANCHOR,
                rationale=(
                    f"Pages that carry both the name and the handle {handle!r} you "
                    f"supplied — a strong pairing when it appears."
                ),
                priority=5,
                anchors_used=["username"],
            )
        )
        out.append(
            ReconQuery(
                query=f'"{handle}"',
                family=FAMILY_ANCHOR,
                rationale=f"Where the supplied handle {handle!r} appears publicly, by itself.",
                priority=20,
                anchors_used=["username"],
            )
        )
    if context.orcid:
        out.append(
            ReconQuery(
                query=f'"{context.orcid}"',
                family=FAMILY_ANCHOR,
                rationale=(
                    "The ORCID iD you supplied, which identifies exactly one researcher "
                    "wherever it is published."
                ),
                priority=1,
                anchors_used=["orcid"],
            )
        )
    if context.occupation:
        out.append(
            ReconQuery(
                query=f'{quoted} "{context.occupation}"',
                family=FAMILY_ANCHOR,
                rationale=(
                    "The name with the occupation you supplied. Weak on its own — many "
                    "people share both — but useful for narrowing a long result list."
                ),
                priority=25,
                anchors_used=["occupation"],
            )
        )
    # City and country are used only *with* the name, never as a standalone
    # locator, and never combined with anything that would sharpen them.
    for place in context.places:
        out.append(
            ReconQuery(
                query=f'{quoted} "{place}"',
                family=FAMILY_ANCHOR,
                rationale=(
                    f"The name with {place}, the place you supplied. Coarse by design: "
                    f"it narrows a result list, it does not locate anyone."
                ),
                priority=25,
                anchors_used=["location"],
            )
        )
    for website in context.websites:
        host = website.split("://", 1)[-1].split("/", 1)[0]
        if host:
            out.append(
                ReconQuery(
                    query=f"{quoted} site:{host}",
                    family=FAMILY_ANCHOR,
                    rationale=f"Pages on {host}, a site you supplied, that name them.",
                    priority=15,
                    anchors_used=["website"],
                )
            )
    return out


def _finalise(queries: list[ReconQuery], context: PersonContext | None = None) -> list[ReconQuery]:
    """Sort, deduplicate and screen the generated list."""
    ordered = sorted(queries, key=lambda item: (item.priority, item.family, item.query))
    seen: set[str] = set()
    unique: list[ReconQuery] = []
    for query in ordered:
        if query.key in seen:
            continue
        seen.add(query.key)
        unique.append(query)
    supplied = supplied_values(context) if context is not None else ()
    return [query for query in unique if is_permitted(query.query, supplied=supplied)][:MAX_QUERIES]


def supplied_values(context: PersonContext) -> tuple[str, ...]:
    """Anchor values the investigator stated, longest first.

    Longest first so masking removes the fullest match rather than a fragment of
    it, which matters when one anchor contains another.
    """
    values = [
        *context.affiliations,
        *context.places,
        *(value for value in (context.occupation,) if value),
    ]
    return tuple(sorted((value for value in values if value.strip()), key=len, reverse=True))


def is_permitted(query: str, *, supplied: Sequence[str] = ()) -> bool:
    """False when a query contains a term this platform will not search for.

    Matched on word boundaries so an anchor legitimately containing a substring
    — "Addressograph Ltd", say — is not rejected by accident.

    ``supplied`` names anchor values the investigator stated. Masking them
    relaxes :data:`CONTEXTUAL_TERMS` only: refusing to search for a stated
    employer because its name contains "medical" protects nobody and makes the
    platform useless to anyone working at a hospital or a medical school.

    :data:`NEVER_SEARCHABLE` is checked against the *unmasked* query, so an
    anchor cannot be used to smuggle "home address" or "date of birth" past this
    screen. That is the whole reason the two sets are separate.
    """
    lowered = query.lower()
    if _contains(lowered, NEVER_SEARCHABLE):
        return False

    masked = lowered
    for value in supplied:
        cleaned = value.strip().lower()
        if cleaned:
            masked = masked.replace(cleaned, " ")
    return not _contains(masked, CONTEXTUAL_TERMS)


def _contains(text: str, terms: frozenset[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in terms)
