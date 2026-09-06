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

from app.collectors.base import (
    BaseCollector,
    CollectorConfiguration,
    CollectorContext,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.correlation.confidence import ConfidenceSignal, default_engine
from app.models.enums import Classification, FindingKind, TargetType
from app.services.normalization import NormalizedTarget

#: Candidates kept per source. A name search is a starting point for a human,
#: not a corpus, and an unbounded list of same-name strangers helps nobody.
MAX_CANDIDATES = 25


def _fold(text: str) -> str:
    """Lower-case and strip punctuation, for tolerant textual comparison."""
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _fold(text).split() if len(token) > 2}


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
    organizations: tuple[str, ...] = ()
    schools: tuple[str, ...] = ()
    country: str | None = None
    city: str | None = None

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
            known_usernames=_strings("known_usernames"),
            profile_urls=_strings("profile_urls"),
            organizations=_strings("organizations"),
            schools=_strings("schools"),
            country=_string("country"),
            city=_string("city"),
        )

    @property
    def affiliations(self) -> tuple[str, ...]:
        return self.organizations + self.schools

    @property
    def places(self) -> tuple[str, ...]:
        return tuple(place for place in (self.city, self.country) if place)

    def is_empty(self) -> bool:
        return not (self.known_usernames or self.profile_urls or self.affiliations or self.places)

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
    corroborated_by: list[str] = field(default_factory=list)


def assess(candidate: PersonCandidate, subject: str, context: PersonContext) -> Assessment:
    """Score one candidate against the subject and the supplied context.

    The name always contributes ``same_person_name``, which is capped low
    enough that it can never on its own suggest a match. Everything above that
    comes from context the investigator supplied independently of the search.
    """
    result = Assessment(signals=[default_engine.signal("same_person_name")])

    if _fold(candidate.name) == _fold(subject):
        result.match_reasons.append(
            f"The source spells the name exactly as searched: {candidate.name!r}"
        )
    else:
        result.match_reasons.append(
            f"The source names {candidate.name!r}, a variant of the searched name"
        )
        result.mismatch_reasons.append(
            f"The spelling differs from the searched name ({subject!r}), "
            f"which may mean a different person"
        )

    matched_url = _match_profile_url(candidate, context)
    if matched_url:
        result.signals.append(
            default_engine.signal("context_profile_url_match", detail=matched_url)
        )
        result.match_reasons.append(
            f"This is a profile URL you supplied for the subject ({matched_url})"
        )
        result.corroborated_by.append("profile_url")

    matched_handle = _match_handle(candidate, context)
    if matched_handle:
        result.signals.append(
            default_engine.signal("context_username_match", detail=matched_handle)
        )
        result.match_reasons.append(
            f"The account handle {matched_handle!r} is one you supplied as known for the subject"
        )
        result.corroborated_by.append("username")

    matched_affiliation = _match_affiliation(candidate, context)
    if matched_affiliation:
        supplied, observed = matched_affiliation
        result.signals.append(default_engine.signal("context_affiliation_match", detail=observed))
        result.match_reasons.append(
            f"The source lists {observed!r}, matching the affiliation you supplied ({supplied!r})"
        )
        result.corroborated_by.append("affiliation")
    elif context.affiliations and candidate.affiliations:
        result.mismatch_reasons.append(
            "None of the affiliations this source lists "
            f"({', '.join(candidate.affiliations[:3])}) match the ones you supplied"
        )

    matched_place = _match_place(candidate, context)
    if matched_place:
        result.signals.append(default_engine.signal("context_location_match", detail=matched_place))
        result.match_reasons.append(
            f"The source places this record in {matched_place}, as you supplied"
        )
        result.corroborated_by.append("location")

    if not result.corroborated_by:
        result.mismatch_reasons.append(
            "Nothing beyond the name connects this record to the subject"
            + ("" if context.is_empty() else " — none of the context you supplied appears in it")
        )
    if context.is_empty():
        result.mismatch_reasons.append(
            "No context was supplied, so no candidate here can be corroborated or ruled out"
        )
    if not candidate.affiliations and not candidate.locations and not candidate.handles:
        result.mismatch_reasons.append(
            "This source publishes nothing else about the record that could be checked"
        )

    return result


def _match_profile_url(candidate: PersonCandidate, context: PersonContext) -> str | None:
    def canon(url: str) -> str:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        host = (parts.hostname or "").lower().removeprefix("www.")
        return f"{host}{parts.path.rstrip('/').lower()}"

    target = canon(candidate.url)
    for supplied in context.profile_urls:
        if target and canon(supplied) == target:
            return supplied
    return None


def _match_handle(candidate: PersonCandidate, context: PersonContext) -> str | None:
    known = {name.strip().lstrip("@").lower() for name in context.known_usernames}
    for handle in candidate.handles:
        if handle.strip().lstrip("@").lower() in known:
            return handle
    return None


def _match_affiliation(
    candidate: PersonCandidate, context: PersonContext
) -> tuple[str, str] | None:
    """Match on shared significant words rather than exact strings.

    "MIT" and "Massachusetts Institute of Technology" will not match, and that
    is the safer failure: a missed corroboration leaves a candidate at name-only
    confidence, whereas a false one would raise a stranger toward the subject.
    """
    for supplied in context.affiliations:
        supplied_tokens = _tokens(supplied)
        if not supplied_tokens:
            continue
        for observed in candidate.affiliations:
            observed_tokens = _tokens(observed)
            if supplied_tokens & observed_tokens:
                return supplied, observed
    return None


def _match_place(candidate: PersonCandidate, context: PersonContext) -> str | None:
    for place in context.places:
        folded = _fold(place)
        if not folded:
            continue
        for observed in candidate.locations:
            if folded in _fold(observed) or _fold(observed) in folded:
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
        candidates, notes = await self.find_candidates(name, target, ctx)

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
