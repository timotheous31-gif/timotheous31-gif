"""Shared machinery for PERSON collectors.

Every free source that can be asked "who is called X?" answers with a list of
*candidates*, not with a person. This module owns what that means, so the six
collectors below it only have to know how to talk to their own API:

* a candidate is keyed on the public page it came from, never on the name, so
  two people who share a name never collapse into one entity;
* a candidate's confidence starts from "the name matched", which is worth very
  little, and rises only when investigator-supplied context independently
  corroborates it;
* both sides of the judgement are recorded — why this may be the person, and
  why it may not — because an investigator has to be able to rule candidates
  out, and a system that only argues for matches is useless for that.

None of these sources needs an API key. That is a requirement, not a
coincidence: the platform must produce useful PERSON results at zero cost.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorConfiguration,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.social import classify_url
from app.core.errors import CollectorUnavailable
from app.correlation.anchors import ANCHOR_REASONS, ANCHOR_RULES
from app.correlation.confidence import ConfidenceSignal, default_engine
from app.models.enums import Classification, FindingKind, TargetType
from app.services.name_variants import (
    EXACT,
    PARTIAL,
    TOKEN_REDUCED,
    VARIANT_LABELS,
    classify_observed_name,
    confidence_rule_for,
)
from app.services.normalization import NormalizedTarget

#: Candidates kept per source. A name search is a starting point for a human,
#: not a corpus, and an unbounded list of same-name strangers helps nobody.
MAX_CANDIDATES = 25


def _fold(text: str) -> str:
    """Lower-case and strip punctuation, for tolerant textual comparison."""
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _fold(text).split() if len(token) > 2}


#: Words that carry no identity of their own inside an organisation name.
#: Dropped before comparison so "Institute of Example Research" and "Example
#: Research Institute" are recognised as one name written two ways.
ORG_STRUCTURE_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "de",
        "del",
        "des",
        "du",
        "el",
        "for",
        "in",
        "la",
        "of",
        "the",
    }
)

#: Characters that introduce an address, a campus or a parenthetical qualifier.
#: Everything from the first one is dropped: "University of Sindh, Jamshoro" and
#: "University of Sindh" are the same employer written with and without where it
#: is.
ORG_QUALIFIER_MARKS: tuple[str, ...] = (",", "(", " - ", " — ", ";", "|")

#: Identifier kinds that name an organisation outright. An agreement on one of
#: these is an agreement about the same registered body, not about its name.
ORG_IDENTIFIER_KEYS: tuple[str, ...] = ("ror", "grid", "wikidata", "isni", "lei")


def normalize_organisation(name: str) -> frozenset[str]:
    """The identity-bearing words of an organisation name, order-insensitive.

    Deliberately strict, because the rule built on it is the strongest signal in
    the set short of an exact identifier. Two names match only when this returns
    the same set for both — never on a shared word, however distinctive.

    An earlier version matched on any single shared word over two characters, so
    "University of Karachi" corroborated "University of Sindh" on the word
    "university". Tightening that to "one shared *distinctive* word" fixed the
    generic collisions and left the specific ones: "Aga Khan University" still
    corroborated "Aga Khan Foundation", and "University of Sindh" still
    corroborated "Sindh Agriculture University". Those are different
    organisations, and a rule worth 0.50 with a floor of 0.50 may not guess.
    """
    text = (name or "").strip()
    if not text:
        return frozenset()
    lowered = text.lower()
    for mark in ORG_QUALIFIER_MARKS:
        index = lowered.find(mark)
        if index > 0:
            lowered = lowered[:index]
    tokens = _fold(lowered).split()
    return frozenset(token for token in tokens if token and token not in ORG_STRUCTURE_WORDS)


def organisation_ids(values: Any) -> frozenset[str]:
    """Explicit organisation identifiers, when a source publishes any."""
    if not isinstance(values, dict):
        return frozenset()
    found = set()
    for key in ORG_IDENTIFIER_KEYS:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            found.add(f"{key}:{value.strip().lower()}")
    return frozenset(found)


# ------------------------------------------------------------ normalisation
#
# Anchors arrive as an investigator typed them. Normalising is what makes an
# anchor comparable to what a source published, and each function stays narrow:
# it folds away notation, never meaning. "T. Samar" is not normalised into
# "Timotheous Samar", because that would merge two different people.


def normalize_handle(value: str) -> str:
    """Fold a handle to its bare, lower-case form (``@Alice`` -> ``alice``)."""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" in text or text.count("/") >= 2:
        # A profile URL was pasted into a username field; take the handle.
        profile = classify_url(text)
        if profile and profile.handle:
            return profile.handle.lower()
    return text.lstrip("@").strip("/").lower()


def normalize_url(value: str) -> str:
    """Canonical form of a public URL, for comparison against a candidate's."""
    text = (value or "").strip()
    if not text:
        return ""
    profile = classify_url(text)
    return profile.url if profile else text.rstrip("/").lower()


def normalize_orcid(value: str) -> str:
    """The bare ORCID iD, whether given as an iD or as an orcid.org URL."""
    text = (value or "").strip()
    if not text:
        return ""
    candidate = text.rsplit("/", 1)[-1].strip().upper()
    return candidate if ORCID_RE.match(candidate) else ""


ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


@dataclass(frozen=True, slots=True)
class PersonContext:
    """Investigator-supplied context, read back off the target.

    This is only ever used to *judge* candidates a public source already
    returned. It is never sent to a source as an extra search term for
    narrowing down a private individual, and it never becomes a finding of its
    own — the investigator already knew it.
    """

    known_usernames: tuple[str, ...] = ()
    profile_urls: tuple[str, ...] = ()
    #: Public sites the subject is known to publish (a homepage, a blog).
    websites: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    schools: tuple[str, ...] = ()
    #: A profession, not a job title at a named employer.
    occupation: str | None = None
    #: Exact public identifiers — the strongest anchors available, because they
    #: identify one record rather than describing a person.
    orcid: str | None = None
    github_username: str | None = None
    country: str | None = None
    city: str | None = None
    #: What the investigator originally typed, preserved verbatim so a report
    #: can explain a match in their own words rather than in normalised form.
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_target(cls, target: NormalizedTarget) -> PersonContext:
        raw = target.attributes.get("context") or {}
        if not isinstance(raw, dict):
            return cls()

        def _strings(key: str) -> tuple[str, ...]:
            value = raw.get(key) or []
            if not isinstance(value, list):
                return ()
            return tuple(str(item).strip() for item in value if str(item).strip())

        def _string(key: str) -> str | None:
            value = raw.get(key)
            text = str(value).strip() if value is not None else ""
            return text or None

        return cls(
            known_usernames=tuple(
                handle for item in _strings("known_usernames") if (handle := normalize_handle(item))
            ),
            profile_urls=tuple(normalize_url(item) for item in _strings("profile_urls")),
            websites=tuple(normalize_url(item) for item in _strings("websites")),
            organizations=_strings("organizations"),
            schools=_strings("schools"),
            occupation=_string("occupation"),
            orcid=normalize_orcid(_string("orcid") or "") or None,
            github_username=normalize_handle(_string("github_username") or "") or None,
            country=_string("country"),
            city=_string("city"),
            raw={key: value for key, value in raw.items() if value not in (None, "", [])},
        )

    @property
    def affiliations(self) -> tuple[str, ...]:
        return self.organizations + self.schools

    @property
    def places(self) -> tuple[str, ...]:
        return tuple(place for place in (self.city, self.country) if place)

    @property
    def all_handles(self) -> tuple[str, ...]:
        """Every handle anchor, including the GitHub one stated separately."""
        handles = list(self.known_usernames)
        if self.github_username and self.github_username not in handles:
            handles.append(self.github_username)
        return tuple(handles)

    @property
    def known_links(self) -> tuple[str, ...]:
        return self.profile_urls + self.websites

    def is_empty(self) -> bool:
        return not (
            self.all_handles
            or self.known_links
            or self.affiliations
            or self.places
            or self.occupation
            or self.orcid
        )

    def describe(self) -> list[str]:
        """What was supplied, for the run stats. Values, not counts, are the
        point: an investigator needs to see what the match was judged against."""
        parts = []
        if self.known_usernames:
            parts.append(f"usernames: {', '.join(self.known_usernames)}")
        if self.profile_urls:
            parts.append(f"profile URLs: {len(self.profile_urls)}")
        if self.organizations:
            parts.append(f"organisations: {', '.join(self.organizations)}")
        if self.schools:
            parts.append(f"schools: {', '.join(self.schools)}")
        if self.occupation:
            parts.append(f"occupation: {self.occupation}")
        if self.orcid:
            parts.append(f"ORCID: {self.orcid}")
        if self.github_username:
            parts.append(f"GitHub: {self.github_username}")
        if self.websites:
            parts.append(f"websites: {len(self.websites)}")
        if self.places:
            parts.append(f"place: {', '.join(self.places)}")
        return parts


@dataclass(slots=True)
class PersonCandidate:
    """One public record that carries the searched-for name.

    A candidate is a *question* — "is this them?" — carrying everything needed
    to answer it. It is never an assertion of identity.
    """

    #: The public page for this record. Also its identity: two candidates with
    #: the same URL are the same record, two with different URLs are not the
    #: same person until something says otherwise.
    url: str
    #: The name exactly as the source spells it.
    name: str
    #: Short human description of the record ("Author with 12 works").
    summary: str = ""
    #: Persistent identifiers the source published (ORCID iD, QID, login…).
    identifiers: dict[str, str] = field(default_factory=dict)
    #: Institutions, employers or groups the source attributes to this record.
    affiliations: list[str] = field(default_factory=list)
    #: Places the source attributes to this record, at city/country coarseness.
    locations: list[str] = field(default_factory=list)
    #: Account handles the source publishes for this record.
    handles: list[str] = field(default_factory=list)
    #: Anything else worth showing, kept small and non-sensitive.
    extra: dict[str, Any] = field(default_factory=dict)
    #: The raw artefact this candidate came from, for the evidence store.
    payload: RawPayload | None = None

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower().removeprefix("www.")


@dataclass(slots=True)
class Assessment:
    """Why a candidate may, or may not, be the person under investigation."""

    signals: list[ConfidenceSignal] = field(default_factory=list)
    match_reasons: list[str] = field(default_factory=list)
    mismatch_reasons: list[str] = field(default_factory=list)
    #: Anchor kinds that matched, e.g. ``["orcid", "affiliation"]``.
    corroborated_by: list[str] = field(default_factory=list)
    #: Anchor kinds the source contradicts. A conflict never subtracts score —
    #: it is surfaced so a human can rule the candidate out themselves.
    conflicts: list[str] = field(default_factory=list)
    #: How the source's spelling of the name relates to the canonical one, and
    #: why. A discovery signal; never an identity claim.
    name_variant_type: str = EXACT
    name_variant_reason: str = ""


def assess(candidate: PersonCandidate, subject: str, context: PersonContext) -> Assessment:
    """Score one candidate against the subject and the supplied anchors.

    The name always contributes a name rule, capped low enough that it can never
    on its own suggest a match — and *which* rule depends on how the source's
    spelling relates to the canonical name. A record published under a shorter
    form of the name fires a weaker rule than one published under the full
    spelling, because a shorter name is shared by more people. Everything above
    that comes from anchors the investigator supplied independently of the
    search, and each anchor kind fires exactly one named rule with its own
    ceiling — so no quantity of weak agreements can substitute for one strong one.
    """
    variant_type, variant_reason = classify_observed_name(candidate.name, subject)
    result = Assessment(
        signals=[default_engine.signal(confidence_rule_for(variant_type))],
        name_variant_type=variant_type,
        name_variant_reason=variant_reason,
    )
    _assess_name(candidate, subject, result)

    for kind, detail in _anchor_matches(candidate, context):
        result.signals.append(default_engine.signal(ANCHOR_RULES[kind], detail=detail))
        result.corroborated_by.append(kind)
        result.match_reasons.append(ANCHOR_REASONS[kind].format(detail=detail))

    _assess_conflicts(candidate, context, result)

    if not result.corroborated_by:
        result.mismatch_reasons.append(
            "Nothing beyond the name connects this record to the subject"
            + ("" if context.is_empty() else " — none of the anchors you supplied appears in it")
        )
    if context.is_empty():
        result.mismatch_reasons.append(
            "No anchors were supplied, so no candidate here can be corroborated or ruled out"
        )
    if not candidate.affiliations and not candidate.locations and not candidate.handles:
        result.mismatch_reasons.append(
            "This source publishes nothing else about the record that could be checked"
        )
    return result


def _assess_name(candidate: PersonCandidate, subject: str, result: Assessment) -> None:
    """Explain the name relationship the variant engine already classified."""
    if result.name_variant_type == EXACT:
        result.match_reasons.append(
            f"The source spells the name exactly as searched: {candidate.name!r}"
        )
        return

    result.match_reasons.append(
        f"{VARIANT_LABELS.get(result.name_variant_type, 'A name variant')}: "
        f"the source names {candidate.name!r}"
    )
    result.mismatch_reasons.append(
        f"The spelling differs from the searched name ({subject!r}), "
        f"which may mean a different person"
    )
    if result.name_variant_reason:
        result.mismatch_reasons.append(result.name_variant_reason)
    if result.name_variant_type in {TOKEN_REDUCED, PARTIAL}:
        result.mismatch_reasons.append(
            "A shorter or partial name is shared by more people than the full one, so "
            "this record needs independent corroboration before it means anything"
        )


def name_relationship(searched: str, declared: str) -> dict[str, str]:
    """How a name a source declares relates to the name that was searched.

    Recorded rather than resolved. A GitHub profile declaring "Timotheous Samar
    Dass" for a search of "Timotheous Samar" is corroboration worth showing, but
    it is not permission to rewrite the target: the investigator named the
    subject, and a source's spelling of a name is a claim by that source. So
    this returns a relationship and a sentence, and nothing anywhere writes the
    declared name back onto the target.
    """
    searched_clean = (searched or "").strip()
    declared_clean = (declared or "").strip()
    if not declared_clean:
        return {"relationship": "undeclared", "explanation": "The source declares no name."}
    if _fold(searched_clean) == _fold(declared_clean):
        return {
            "relationship": "exact",
            "explanation": (
                f"The source spells the name exactly as searched ({declared_clean!r})."
            ),
        }

    searched_parts = _fold(searched_clean).split()
    declared_parts = _fold(declared_clean).split()
    searched_set, declared_set = set(searched_parts), set(declared_parts)

    if searched_set and searched_set < declared_set:
        extra = [part for part in declared_parts if part not in searched_set]
        return {
            "relationship": "extends_searched_name",
            "explanation": (
                f"The source declares {declared_clean!r}, which carries every part of the "
                f"searched name {searched_clean!r} plus {', '.join(extra)!r}. The name under "
                f"investigation is unchanged."
            ),
        }
    if declared_set and declared_set < searched_set:
        return {
            "relationship": "shortens_searched_name",
            "explanation": (
                f"The source declares {declared_clean!r}, a shorter form of the searched "
                f"name {searched_clean!r}. The name under investigation is unchanged."
            ),
        }
    if searched_set & declared_set:
        shared = sorted(searched_set & declared_set)
        return {
            "relationship": "partial_overlap",
            "explanation": (
                f"The source declares {declared_clean!r}, sharing {', '.join(shared)!r} with "
                f"the searched name {searched_clean!r} and differing elsewhere."
            ),
        }
    return {
        "relationship": "unrelated",
        "explanation": (
            f"The source declares {declared_clean!r}, which shares no name part with the "
            f"searched name {searched_clean!r}."
        ),
    }


def _anchor_matches(candidate: PersonCandidate, context: PersonContext) -> list[tuple[str, str]]:
    """Every anchor kind that matches, each at most once.

    Ordered strongest first, and each kind contributes a single signal however
    many values agree: five matching affiliations are one affiliation match, not
    five, because they are one fact observed once.
    """
    matches: list[tuple[str, str]] = []

    matched_url = _match_link(candidate, context.profile_urls)
    if matched_url:
        matches.append(("profile_url", matched_url))

    supplied_orcid = normalize_orcid(context.orcid or "")
    if supplied_orcid and supplied_orcid == _candidate_orcid(candidate):
        matches.append(("orcid", supplied_orcid))

    github = _candidate_identifier(candidate, "github_login") or _handle_for_platform(
        candidate, "github"
    )
    supplied_github = normalize_handle(context.github_username or "")
    if supplied_github and github and supplied_github == normalize_handle(github):
        matches.append(("github_username", github))

    matched_handle = _match_handle(candidate, context)
    if matched_handle and not any(kind == "github_username" for kind, _ in matches):
        matches.append(("username", matched_handle))

    matched_site = _match_link(candidate, context.websites)
    if matched_site and not any(kind == "profile_url" for kind, _ in matches):
        matches.append(("website", matched_site))

    matched_affiliation = _match_affiliation(candidate, context)
    if matched_affiliation:
        matches.append(("affiliation", matched_affiliation[1]))

    matched_occupation = _match_occupation(candidate, context)
    if matched_occupation:
        matches.append(("occupation", matched_occupation))

    matched_place = _match_place(candidate, context)
    if matched_place:
        matches.append(("location", matched_place))

    return matches


def anchor_matches(candidate: PersonCandidate, context: PersonContext) -> list[tuple[str, str]]:
    """Anchor kinds matching this candidate, for callers outside collection.

    An investigator-imported result is held to the same anchor model as a
    collected one: whether an anchor matches is a property of the record and the
    subject, not of how the record was found. Exposed rather than reimplemented
    so the import path cannot drift from the collectors.
    """
    return _anchor_matches(candidate, context)


def _assess_conflicts(
    candidate: PersonCandidate, context: PersonContext, result: Assessment
) -> None:
    """Record where the source positively disagrees with an anchor.

    Only a *stated* disagreement counts. A source that publishes no affiliation
    is silent, not contradictory, and silence must never read as a conflict.
    """
    if (
        context.affiliations
        and candidate.affiliations
        and "affiliation" not in result.corroborated_by
    ):
        result.conflicts.append("affiliation")
        result.mismatch_reasons.append(
            f"The affiliations this source lists ({', '.join(candidate.affiliations[:3])}) "
            f"do not include any you supplied"
        )
    if context.places and candidate.locations and "location" not in result.corroborated_by:
        result.conflicts.append("location")
        result.mismatch_reasons.append(
            f"This source places the record in {', '.join(candidate.locations[:3])}, "
            f"not {', '.join(context.places)}"
        )
    orcid = _candidate_orcid(candidate)
    supplied_orcid = normalize_orcid(context.orcid or "")
    if supplied_orcid and orcid and supplied_orcid != orcid:
        result.conflicts.append("orcid")
        result.mismatch_reasons.append(
            f"This record carries ORCID {orcid}, which is not the {supplied_orcid} you supplied — "
            f"a different researcher"
        )


def _candidate_orcid(candidate: PersonCandidate) -> str:
    return normalize_orcid(str(candidate.identifiers.get("orcid", "")))


def _candidate_identifier(candidate: PersonCandidate, key: str) -> str:
    return str(candidate.identifiers.get(key, "")).strip().lower()


def _handle_for_platform(candidate: PersonCandidate, platform: str) -> str:
    profile = classify_url(candidate.url)
    if profile and profile.platform == platform and profile.handle:
        return profile.handle
    return ""


def _match_link(candidate: PersonCandidate, supplied: tuple[str, ...]) -> str | None:
    """Match the candidate's own URL, or a link it publishes, against anchors."""
    candidate_links = {normalize_url(candidate.url)}
    for value in candidate.extra.values():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            candidate_links.add(normalize_url(value))
    for value in supplied:
        # Normalise the supplied side here too: a context built directly in
        # code has not been through ``from_target``, and a trailing slash must
        # not be the difference between a match and a miss.
        if value and normalize_url(value) in candidate_links:
            return value
    return None


def _match_handle(candidate: PersonCandidate, context: PersonContext) -> str | None:
    """Match a candidate's handle against the *platform-agnostic* handles.

    ``known_usernames`` only, never ``all_handles``. ``github_username`` is an
    anchor about GitHub: the investigator said *this account on that platform*
    is the subject's, and the GitHub branch above is where it belongs. Letting
    it into the general handle pool made a LinkedIn account that happens to use
    the same string score as though the investigator had vouched for it — and
    a shared handle is not a shared owner. ``known_usernames`` carries no such
    platform, so it still matches anywhere.
    """
    known = {normalize_handle(item) for item in context.known_usernames}
    for handle in candidate.handles:
        if normalize_handle(handle) in known:
            return handle
    return None


def _match_affiliation(
    candidate: PersonCandidate, context: PersonContext
) -> tuple[str, str] | None:
    """Match a whole organisation name, or an explicit organisation identifier.

    Two names match when :func:`normalize_organisation` returns the same word set
    for both — so an address or campus suffix, a different word order, and "the"
    are tolerated, and nothing else is. "MIT" and "Massachusetts Institute of
    Technology" do not match, and neither do "Aga Khan University" and "Aga Khan
    Foundation". Both are missed corroborations, which leave a candidate at
    name-only confidence; the alternative is a false one, which moves a stranger
    toward the subject and does it invisibly.
    """
    supplied_org_ids = organisation_ids(context.raw.get("organization_ids"))
    observed_org_ids = organisation_ids(candidate.extra.get("organization_ids"))
    shared_ids = supplied_org_ids & observed_org_ids
    if shared_ids:
        identifier = sorted(shared_ids)[0]
        return identifier, identifier

    for supplied in context.affiliations:
        supplied_tokens = normalize_organisation(supplied)
        if not supplied_tokens:
            continue
        for observed in candidate.affiliations:
            if normalize_organisation(observed) == supplied_tokens:
                return supplied, observed
    return None


def _match_occupation(candidate: PersonCandidate, context: PersonContext) -> str | None:
    """Match the supplied profession as a whole, not word by word.

    A single shared word made "assistant professor" match "assistant manager".
    Either the phrase appears in what the source publishes, or every word of it
    does; one word in common is not a profession in common.
    """
    if not context.occupation:
        return None
    wanted = _tokens(context.occupation)
    if not wanted:
        return None
    phrase = _fold(context.occupation)
    haystack = [*candidate.extra.get("occupations", []), candidate.summary]
    for observed in haystack:
        if not isinstance(observed, str):
            continue
        folded = _fold(observed)
        if phrase and phrase in folded:
            return context.occupation
        if wanted <= _tokens(observed):
            return context.occupation
    return None


def _match_place(candidate: PersonCandidate, context: PersonContext) -> str | None:
    """Match a place on whole words, not on substrings.

    Substring containment made "Sindh" match "Sindhudurg" — two places 1,500km
    apart. Coarse is fine here (the anchors are a city or a country) but wrong is
    not, so the supplied place's words must all appear as words.
    """
    for place in context.places:
        wanted = {token for token in _fold(place).split() if token}
        if not wanted:
            continue
        for observed in candidate.locations:
            if wanted <= {token for token in _fold(observed).split() if token}:
                return place
    return None


class PersonSourceCollector(BaseCollector):
    """Base for collectors that answer "which public records carry this name?".

    Subclasses implement :meth:`find_candidates` and nothing else; this class
    owns the shared contract — bounded results, candidate keying, assessment,
    and the shape of the emitted finding.
    """

    supported_targets = [TargetType.PERSON]
    requires_api_key = False
    #: Human label for the source, shown against every candidate in the UI.
    source_label: str = ""
    #: What the source is, for the "no key needed" story in Settings.
    free_access_note: str = "Public API, no account or API key required."

    def configuration(self) -> CollectorConfiguration:
        return CollectorConfiguration(
            required_settings=[],
            configured=True,
            mode="free",
            detail=self.free_access_note,
        )

    @abc.abstractmethod
    async def find_candidates(
        self, name: str, target: NormalizedTarget, ctx: CollectorContext
    ) -> tuple[list[PersonCandidate], list[str]]:
        """Return ``(candidates, notes)`` for ``name``.

        Implementations query one public endpoint and translate its response.
        They must not filter on the supplied context: the context is used to
        *judge* what the source returned, and silently dropping the records it
        does not corroborate would hide exactly the same-name strangers an
        investigator needs to see in order to rule them out.
        """

    async def collect(self, target: NormalizedTarget, ctx: CollectorContext) -> CollectorResult:
        name = str(target.attributes.get("display_name", target.value))
        context = PersonContext.from_target(target)
        try:
            candidates, notes = await self.find_candidates(name, target, ctx)
        except (httpx.ProxyError, httpx.ConnectError) as exc:
            # The endpoint could not be reached at all: an egress policy, a
            # corporate proxy, or the platform's own edge refused the
            # connection. That is a fact about where this deployment runs, not
            # a defect in the collector, so it is reported as unavailable with
            # the reason rather than as a failure. Timeouts and every other
            # error deliberately fall through and are recorded as failures,
            # because those are the ones worth investigating.
            raise CollectorUnavailable(
                f"{self.source_label} could not be reached from this deployment "
                f"({type(exc).__name__}: {exc}). The endpoint is public; the "
                f"connection was refused before any request was answered. No "
                f"attempt is made to route around it."
            ) from exc

        result = CollectorResult(
            stats={
                "source": self.name,
                "candidates": min(len(candidates), MAX_CANDIDATES),
                "context_supplied": context.describe(),
            }
        )
        result.notes.extend(notes)

        seen: set[str] = set()
        for candidate in candidates[:MAX_CANDIDATES]:
            if not candidate.url or candidate.url in seen:
                continue
            seen.add(candidate.url)
            draft = self.build_finding(candidate, name, target, context)
            result.add(draft, candidate.payload)

        if not seen and not notes:
            result.notes.append(f"{self.source_label} returned no record matching {name!r}")
        return result

    def build_finding(
        self,
        candidate: PersonCandidate,
        subject_name: str,
        target: NormalizedTarget,
        context: PersonContext,
    ) -> FindingDraft:
        assessment = assess(candidate, subject_name, context)
        score = default_engine.score(assessment.signals)
        return FindingDraft(
            kind=FindingKind.PERSON_CANDIDATE,
            title=f"{candidate.name} — {self.source_label}",
            summary=(
                candidate.summary
                or f"A {self.source_label} record carrying the name {subject_name!r}."
            ),
            data={
                "url": candidate.url,
                "host": candidate.host,
                "title": candidate.name,
                "snippet": candidate.summary,
                "source": self.name,
                "source_label": self.source_label,
                "candidate_name": candidate.name,
                "subject_name": subject_name,
                "subject_value": target.value,
                "candidate_key": candidate.url,
                "identifiers": candidate.identifiers,
                "affiliations": candidate.affiliations,
                "locations": candidate.locations,
                "handles": candidate.handles,
                "match_reasons": assessment.match_reasons,
                "mismatch_reasons": assessment.mismatch_reasons,
                "corroborated_by": assessment.corroborated_by,
                # How this source's spelling relates to the canonical name, and
                # why. Recorded, never applied: the target keeps the name the
                # investigator supplied.
                "name_variant_type": assessment.name_variant_type,
                "name_variant_reason": assessment.name_variant_reason,
                "canonical_target": subject_name,
                **candidate.extra,
            },
            source_url=candidate.url,
            confidence=score.score,
            confidence_reasons=[*score.reasons, *assessment.mismatch_reasons],
            # A public record naming a person is personal data even when the
            # person published it themselves.
            classification=Classification.PERSONAL,
            dedupe_key=f"person-candidate:{target.value}:{candidate.url}",
        )
