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
from dataclasses import dataclass, field, replace
from typing import Any

from app.collectors.capabilities import search_platforms
from app.collectors.person import PersonContext, normalize_handle
from app.services.name_variants import (
    EXACT,
    TOKEN_REDUCED,
    VARIANT_LABELS,
    NameVariant,
    generate_variants,
)

#: Query families, ordered by how much signal they usually carry.
FAMILY_GENERAL = "general"
FAMILY_SOCIAL = "social"
FAMILY_ACADEMIC = "academic"
FAMILY_DOCUMENT = "document"
FAMILY_ANCHOR = "anchor"
FAMILY_IMAGE = "image"
FAMILY_HANDLE = "handle"


#: Public platforms worth a targeted query, read from the capability registry
#: rather than restated here. The registry already knows which platforms this
#: codebase recognises and which ``site:`` filter reaches their profiles; a
#: second list would drift the moment one was updated and the other was not.
def social_sites() -> tuple[tuple[str, str], ...]:
    """``(label, site filter)`` for each searchable platform, worklist order.

    A platform may contribute more than one filter, narrowest first:
    ``site:linkedin.com/in`` returns personal profiles and nothing else, which
    is what a PERSON search wants, while ``site:linkedin.com`` also finds the
    posts and pages that mention them. Both are worth running, so both are
    generated and the narrow one sorts first.
    """
    return tuple(
        (capability.display_name, site_filter)
        for capability in search_platforms()
        for site_filter in capability.search_filters
    )


#: Kept as a module attribute for readability at call sites and in tests.
SOCIAL_SITES: tuple[tuple[str, str], ...] = social_sites()

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
MAX_QUERIES = 60
#: Queries across all five stages of the staged plan. Larger than a single
#: family's cap because the stages are worked one at a time, but still a
#: worklist: an investigator reads these, they are not fed to a machine.
MAX_STAGED_QUERIES = 40
#: Discovered anchors turned into queries. Each costs two searches, and an
#: unbounded list of claims read off candidate pages is a crawl.
MAX_DISCOVERED_ANCHORS = 4


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
    #: The name spelling this query searches for, and how it relates to the
    #: canonical one. A hit on a reduced spelling is worth less than a hit on
    #: the full one, and a report cannot say so unless the query records which
    #: it used.
    name_variant: str = ""
    variant_type: str = EXACT
    #: Which stage of the plan it belongs to (1-5).
    stage: int = 1

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
            "name_variant": self.name_variant,
            "variant_type": self.variant_type,
            "variant_label": VARIANT_LABELS.get(self.variant_type, self.variant_type),
            "stage": self.stage,
        }


def _quoted(name: str) -> str:
    return f'"{name}"'


def generate_queries(
    name: str,
    context: PersonContext | None = None,
    *,
    also_known_as: Sequence[str] = (),
) -> list[ReconQuery]:
    """Build the recon query list for ``name`` and its anchors.

    Deterministic: the same inputs always produce the same list in the same
    order, so a case can be re-opened and the investigator sees what they saw
    before. Duplicates are collapsed on the query text, keeping the first
    (highest-priority) occurrence and its rationale.

    ``also_known_as`` carries fuller spellings a public source declared — a
    profile that says "Timotheous Samar Dass" for a search of "Timotheous
    Samar". Searching the fuller name usually returns far fewer strangers.
    These are *additional* searches: the name under investigation is still the
    one the investigator supplied, and nothing here rewrites it.
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
    queries.extend(_declared_name_queries(display, also_known_as))
    queries.extend(_social_queries(quoted, display))
    queries.extend(_image_queries(quoted, context))
    queries.extend(_academic_queries(quoted))
    queries.extend(_anchor_queries(quoted, context))

    return _finalise(queries, context)


def _declared_name_queries(display: str, also_known_as: Sequence[str]) -> list[ReconQuery]:
    """Searches for a fuller name a public source declared."""
    out: list[ReconQuery] = []
    seen = {" ".join(display.lower().split())}
    for raw in also_known_as:
        variant = " ".join(str(raw or "").split())
        key = variant.lower()
        if not variant or key in seen:
            continue
        seen.add(key)
        out.append(
            ReconQuery(
                query=_quoted(variant),
                family=FAMILY_GENERAL,
                rationale=(
                    f"A public source declares the fuller name {variant!r}. Searching it "
                    f"returns far fewer same-name strangers than {display!r} alone. The "
                    f"name under investigation is unchanged."
                ),
                priority=8,
                anchors_used=["declared_name"],
            )
        )
    return out


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
        # A path in the filter means it reaches profiles specifically, so it
        # runs before the whole-domain version.
        narrow = "/" in site
        out.append(
            ReconQuery(
                query=f"{quoted} site:{site}",
                family=FAMILY_SOCIAL,
                rationale=(
                    f"Public {label} profile pages only — the narrowest search for a person."
                    if narrow
                    else f"Public {label} pages of any kind that name them."
                ),
                priority=28 if narrow else 30,
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
        # A handle paired with a platform name is how an investigator actually
        # chases an account on a platform this codebase may not query itself.
        # Finding the same handle there is a lead, not an identification.
        for capability in search_platforms():
            if not capability.handle_check_supported:
                out.append(
                    ReconQuery(
                        query=f'"{handle}" {capability.display_name}',
                        family=FAMILY_HANDLE,
                        rationale=(
                            f"Where the handle {handle!r} appears alongside "
                            f"{capability.display_name}. The same handle on another "
                            f"platform is a lead to check, never proof of the same owner."
                        ),
                        priority=22,
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


# ----------------------------------------------------------- the staged plan
#
# A single list of forty-six queries is a wall, not a workflow. An investigator
# works outward: the name, then the spellings a source might have used, then the
# things they already know, then the things the first passes discovered, then the
# targeted sweeps. The stages below are that order, made explicit — so the UI can
# show one at a time and a report can say which stage produced a lead.

STAGE_CANONICAL = 1
STAGE_VARIANTS = 2
STAGE_SUPPLIED_ANCHORS = 3
STAGE_DISCOVERED_ANCHORS = 4
STAGE_TARGETED = 5

STAGE_TITLES: dict[int, str] = {
    STAGE_CANONICAL: "The name as you supplied it",
    STAGE_VARIANTS: "Spellings a public source might use",
    STAGE_SUPPLIED_ANCHORS: "Paired with what you already know",
    STAGE_DISCOVERED_ANCHORS: "Paired with what the investigation found",
    STAGE_TARGETED: "Targeted platform, image and document sweeps",
}

STAGE_PURPOSES: dict[int, str] = {
    STAGE_CANONICAL: (
        "The plainest search. Broad, and mostly other people — but it is where the "
        "obvious public footprint shows up."
    ),
    STAGE_VARIANTS: (
        "The same search under shorter or differently punctuated spellings. This is "
        "usually what finds a real footprint; a hit here is a lead, not a match, "
        "because a shorter name is shared by more people."
    ),
    STAGE_SUPPLIED_ANCHORS: (
        "The name paired with an employer, a school, a handle or a place you "
        "supplied. These return the fewest strangers, so run them first."
    ),
    STAGE_DISCOVERED_ANCHORS: (
        "The name paired with something a public source actually published about a "
        "candidate — an employer, a role, a field. Only claims with provenance "
        "appear here; nothing is invented."
    ),
    STAGE_TARGETED: (
        "Platform, image and document sweeps. Broadest, and best run once the "
        "stages above have told you which spelling and which anchor to trust."
    ),
}


@dataclass(frozen=True, slots=True)
class DiscoveredAnchor:
    """Something a public source published about a candidate, with its source.

    The provenance is not decoration. Generating a query from "communications
    professional" is only legitimate because a page we actually read said so —
    an anchor without a source is a guess, and a guess in a query becomes a
    guess in a report.
    """

    kind: str
    value: str
    #: The URL that published it.
    source_url: str
    source_label: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value.strip().lower()}"


@dataclass(frozen=True, slots=True)
class ReconStage:
    """One stage of the plan."""

    number: int
    title: str
    purpose: str
    queries: list[ReconQuery] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.number,
            "title": self.title,
            "purpose": self.purpose,
            "queries": [query.as_dict() for query in self.queries],
        }


@dataclass(frozen=True, slots=True)
class ReconPlan:
    """The staged plan, plus the variants and anchors it was built from."""

    canonical: str
    variants: list[NameVariant] = field(default_factory=list)
    stages: list[ReconStage] = field(default_factory=list)
    discovered: list[DiscoveredAnchor] = field(default_factory=list)

    @property
    def queries(self) -> list[ReconQuery]:
        return [query for stage in self.stages for query in stage.queries]

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "variants": [variant.to_dict() for variant in self.variants],
            "stages": [stage.as_dict() for stage in self.stages],
            "discovered_anchors": [
                {
                    "kind": anchor.kind,
                    "value": anchor.value,
                    "source_url": anchor.source_url,
                    "source_label": anchor.source_label,
                }
                for anchor in self.discovered
            ],
        }


def staged_plan(
    name: str,
    context: PersonContext | None = None,
    *,
    variants: list[NameVariant] | None = None,
    discovered: list[DiscoveredAnchor] | None = None,
    also_known_as: Sequence[str] = (),
) -> ReconPlan:
    """Build the five-stage plan for ``name``.

    Every query carries the spelling it searches and the stage it belongs to, so
    a result ingested from it can say how it was found. The same screening as
    :func:`generate_queries` applies — a stage cannot smuggle a query past it.
    """
    canonical = " ".join((name or "").split())
    if not canonical:
        return ReconPlan(canonical="")

    context = context or PersonContext()
    spellings = variants if variants is not None else generate_variants(canonical)
    anchors = list(discovered or [])
    supplied = supplied_values(context)

    def keep(queries: list[ReconQuery]) -> list[ReconQuery]:
        return [item for item in queries if is_permitted(item.query, supplied=supplied)]

    exact = spellings[0] if spellings else None
    stage_one = keep(
        [
            ReconQuery(
                query=_quoted(canonical),
                family=FAMILY_GENERAL,
                rationale="The plainest search: every public page that names them.",
                priority=10,
                name_variant=canonical,
                variant_type=exact.variant_type if exact else EXACT,
                stage=STAGE_CANONICAL,
            )
        ]
    )

    stage_two: list[ReconQuery] = []
    for variant in spellings[1:]:
        stage_two.append(
            ReconQuery(
                query=_quoted(variant.value),
                family=FAMILY_GENERAL,
                rationale=variant.reason,
                priority=12,
                anchors_used=["name_variant"],
                name_variant=variant.value,
                variant_type=variant.variant_type,
                stage=STAGE_VARIANTS,
            )
        )
    for extra in also_known_as:
        declared = " ".join(str(extra or "").split())
        if declared and declared.lower() != canonical.lower():
            stage_two.append(
                ReconQuery(
                    query=_quoted(declared),
                    family=FAMILY_GENERAL,
                    rationale=(
                        f"A public source declares the fuller name {declared!r}. Searching it "
                        f"returns far fewer same-name strangers. The name under investigation "
                        f"is unchanged."
                    ),
                    priority=8,
                    anchors_used=["declared_name"],
                    name_variant=declared,
                    variant_type=EXACT,
                    stage=STAGE_VARIANTS,
                )
            )
    stage_two = keep(stage_two)

    stage_three = keep(
        [
            replace(query, stage=STAGE_SUPPLIED_ANCHORS, name_variant=canonical)
            for query in _anchor_queries(_quoted(canonical), context)
        ]
    )

    stage_four = keep(_discovered_queries(canonical, spellings, anchors))

    targeted: list[ReconQuery] = [
        *_social_queries(_quoted(canonical), canonical),
        *_image_queries(_quoted(canonical), context),
        *_academic_queries(_quoted(canonical)),
    ]
    # The strongest reduced spelling also gets the platform sweep: that is the
    # spelling a public profile is most likely to be published under.
    reduced = next((item for item in spellings[1:] if item.variant_type == TOKEN_REDUCED), None)
    if reduced is not None:
        targeted.extend(
            replace(query, name_variant=reduced.value, variant_type=reduced.variant_type)
            for query in _social_queries(_quoted(reduced.value), reduced.value)
        )
    stage_five = keep(
        [
            replace(
                query,
                stage=STAGE_TARGETED,
                name_variant=query.name_variant or canonical,
                variant_type=query.variant_type,
            )
            for query in targeted
        ]
    )

    stages = [
        ReconStage(STAGE_CANONICAL, STAGE_TITLES[1], STAGE_PURPOSES[1], _dedupe(stage_one)),
        ReconStage(STAGE_VARIANTS, STAGE_TITLES[2], STAGE_PURPOSES[2], _dedupe(stage_two)),
        ReconStage(
            STAGE_SUPPLIED_ANCHORS, STAGE_TITLES[3], STAGE_PURPOSES[3], _dedupe(stage_three)
        ),
        ReconStage(
            STAGE_DISCOVERED_ANCHORS, STAGE_TITLES[4], STAGE_PURPOSES[4], _dedupe(stage_four)
        ),
        ReconStage(STAGE_TARGETED, STAGE_TITLES[5], STAGE_PURPOSES[5], _dedupe(stage_five)),
    ]

    # Deduplicate across stages, keeping the earliest (narrowest) appearance, and
    # cap the whole plan: five stages of twenty is still a wall.
    seen: set[str] = set()
    trimmed: list[ReconStage] = []
    budget = MAX_STAGED_QUERIES
    for stage in stages:
        kept: list[ReconQuery] = []
        for query in stage.queries:
            if query.key in seen or budget <= 0:
                continue
            seen.add(query.key)
            budget -= 1
            kept.append(query)
        trimmed.append(replace(stage, queries=kept))

    return ReconPlan(
        canonical=canonical, variants=list(spellings), stages=trimmed, discovered=anchors
    )


def _dedupe(queries: list[ReconQuery]) -> list[ReconQuery]:
    ordered = sorted(queries, key=lambda item: (item.priority, item.family, item.query))
    seen: set[str] = set()
    unique: list[ReconQuery] = []
    for query in ordered:
        if query.key in seen:
            continue
        seen.add(query.key)
        unique.append(query)
    return unique


def _discovered_queries(
    canonical: str, spellings: list[NameVariant], anchors: list[DiscoveredAnchor]
) -> list[ReconQuery]:
    """Queries pairing a spelling with something a source actually published.

    The strongest reduced spelling is used alongside the canonical one, because
    the discovered claim and the shorter name usually come from the same page —
    that pairing is the one most likely to find more of the same footprint.
    """
    if not anchors:
        return []
    reduced = next((item for item in spellings[1:] if item.variant_type == TOKEN_REDUCED), None)
    targets = [(canonical, EXACT)]
    if reduced is not None:
        targets.append((reduced.value, reduced.variant_type))

    out: list[ReconQuery] = []
    for anchor in anchors[:MAX_DISCOVERED_ANCHORS]:
        value = anchor.value.strip()
        if not value:
            continue
        for spelling, variant_type in targets:
            out.append(
                ReconQuery(
                    query=f'"{spelling}" "{value}"',
                    family=FAMILY_ANCHOR,
                    rationale=(
                        f"{anchor.source_label or 'A public source'} published "
                        f"{value!r} for a candidate ({anchor.source_url}). Pairing it with "
                        f"{spelling!r} narrows the search to that person's footprint."
                    ),
                    priority=6,
                    anchors_used=[f"discovered_{anchor.kind}"],
                    name_variant=spelling,
                    variant_type=variant_type,
                    stage=STAGE_DISCOVERED_ANCHORS,
                )
            )
    return out


#: Keys on a persisted finding that carry a claim about a candidate, and the
#: anchor kind each becomes. Closed, because an anchor built from a key nobody
#: vetted is an anchor built from whatever a page happened to contain.
DISCOVERED_CLAIM_KEYS: tuple[tuple[str, str], ...] = (
    ("company", "organization"),
    ("repository_homepage", "website"),
)
#: Claim *kinds* read from the structured facts a profile page stated.
DISCOVERED_FACT_KINDS: dict[str, str] = {
    "employer": "organization",
    "occupation": "occupation",
    "professional_field": "field",
}


def discovered_anchors(session: Any, case_id: Any) -> list[DiscoveredAnchor]:
    """Claims public sources published about candidates in this case.

    Only values that came with a URL. A query built from "communications
    professional" is legitimate because a page we read said so, and the rationale
    cites that page — so an investigator can see where a search term came from
    and reject it. Without the source it would be the platform inventing an
    anchor and then searching for it, which is how a guess becomes a finding.
    """
    from sqlalchemy import select

    from app.models import Finding

    found: dict[str, DiscoveredAnchor] = {}
    for finding in session.scalars(select(Finding).where(Finding.case_id == case_id)):
        data = finding.data or {}
        source_url = str(data.get("url") or finding.source_url or "")
        label = str(data.get("source_label") or finding.collector)
        if not source_url:
            continue

        for key, kind in DISCOVERED_CLAIM_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                anchor = DiscoveredAnchor(
                    kind=kind,
                    value=value.strip(),
                    source_url=source_url,
                    source_label=label,
                )
                found.setdefault(anchor.key, anchor)

        for entry in data.get("readme_facts") or []:
            if not isinstance(entry, dict):
                continue
            fact_kind = DISCOVERED_FACT_KINDS.get(str(entry.get("kind", "")))
            value = entry.get("value")
            if fact_kind and isinstance(value, str) and value.strip():
                anchor = DiscoveredAnchor(
                    kind=fact_kind,
                    value=value.strip(),
                    source_url=str(entry.get("source_url") or source_url),
                    source_label=label,
                )
                found.setdefault(anchor.key, anchor)

        for affiliation in data.get("affiliations") or []:
            if isinstance(affiliation, str) and affiliation.strip():
                anchor = DiscoveredAnchor(
                    kind="organization",
                    value=affiliation.strip(),
                    source_url=source_url,
                    source_label=label,
                )
                found.setdefault(anchor.key, anchor)

    # Organisations first: an employer narrows a name search far more than a
    # field does, and the plan's budget is spent top-down.
    order = {"organization": 0, "occupation": 1, "field": 2, "website": 3}
    return sorted(found.values(), key=lambda item: (order.get(item.kind, 9), item.value))[
        :MAX_DISCOVERED_ANCHORS
    ]
