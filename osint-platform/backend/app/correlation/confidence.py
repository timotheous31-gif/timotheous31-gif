"""Confidence scoring.

Every score this platform produces is accompanied by the reasons that produced
it. That is not decoration: an investigator has to be able to see *why* a link
is rated 0.85 and disagree with it. Scores are therefore computed from named,
configurable rules rather than from an opaque formula.

Combination is deliberately conservative. Evidence is combined with a
"noisy-OR" so that several weak signals can raise a score, but the result never
exceeds the ceiling of the strongest *class* of evidence present. In practice
that means no quantity of username matches can ever reach the confidence of a
single self-published link.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.models.enums import MatchStrength

#: The interpretation bands the API and UI use.
LIKELY_MATCH_THRESHOLD = 0.90
PROBABLE_MATCH_THRESHOLD = 0.70
POSSIBLE_MATCH_THRESHOLD = 0.50

#: Correlation never merges entities below this score; a human decides.
AUTO_MERGE_THRESHOLD = 0.95


@dataclass(frozen=True, slots=True)
class ConfidenceRule:
    """One named evidence rule."""

    key: str
    #: Score contributed when this rule fires.
    score: float
    #: Human-readable justification recorded on the finding or relationship.
    reason: str
    #: Nothing combining this class of evidence may exceed this value.
    ceiling: float = 1.0

    def signal(self) -> ConfidenceSignal:
        return ConfidenceSignal(
            key=self.key, score=self.score, reason=self.reason, ceiling=self.ceiling
        )


@dataclass(frozen=True, slots=True)
class ConfidenceSignal:
    """A rule that actually fired, optionally with extra context."""

    key: str
    score: float
    reason: str
    ceiling: float = 1.0

    def with_detail(self, detail: str) -> ConfidenceSignal:
        return replace(self, reason=f"{self.reason} ({detail})")


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """The outcome of scoring: a number, a band, and why."""

    score: float
    strength: MatchStrength
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    @property
    def auto_mergeable(self) -> bool:
        """Only the very strongest evidence permits an automatic merge."""
        return self.score >= AUTO_MERGE_THRESHOLD


#: The default ruleset. Values follow the platform's documented scoring policy.
DEFAULT_RULES: dict[str, ConfidenceRule] = {
    rule.key: rule
    for rule in (
        ConfidenceRule(
            "verified_link",
            0.95,
            "Both endpoints publish a link to each other, which each owner controls",
        ),
        ConfidenceRule(
            "site_links_profile",
            0.90,
            "The website publishes a link to this profile, so its owner asserts the association",
        ),
        ConfidenceRule(
            "profile_links_site",
            0.90,
            "The profile publishes a link to this website, so the account holder asserts it",
        ),
        ConfidenceRule(
            "authoritative_record",
            0.95,
            "Stated by the authoritative registry or protocol for this fact",
        ),
        ConfidenceRule(
            "same_username_shared_website",
            0.85,
            "The same username is used on both platforms and both link to the same website",
            ceiling=0.85,
        ),
        ConfidenceRule(
            "same_username_matching_bio",
            0.70,
            "The same username is used and the profile biographies correspond",
            ceiling=0.70,
        ),
        ConfidenceRule(
            "same_unique_username",
            0.50,
            "The same distinctive username appears on both platforms",
            ceiling=0.60,
        ),
        ConfidenceRule(
            "same_common_username",
            0.25,
            "The same username appears, but it is short or common enough to collide by chance",
            ceiling=0.40,
        ),
        ConfidenceRule(
            "shared_infrastructure",
            0.60,
            "The endpoints share hosting infrastructure, which many unrelated parties may do",
            ceiling=0.65,
        ),
        ConfidenceRule(
            "certificate_covers_host",
            0.85,
            "A certificate recorded in a public CT log names this host",
        ),
        ConfidenceRule(
            "dns_resolution",
            0.95,
            "Resolved from public DNS, which is authoritative for this record",
        ),
        ConfidenceRule(
            "same_organization_name",
            0.40,
            "The organisation names correspond after normalisation",
            ceiling=0.55,
        ),
        ConfidenceRule(
            "same_person_name",
            0.15,
            "Only the displayed personal name matches, which does not identify anyone: "
            "names are shared by many people and are not identifiers",
            ceiling=0.30,
        ),
        ConfidenceRule(
            "context_profile_url_match",
            0.85,
            "This is a profile URL the investigator supplied for the subject, so the "
            "association is asserted by them rather than inferred",
            ceiling=0.90,
        ),
        ConfidenceRule(
            "anchor_orcid_match",
            0.88,
            "The record carries the exact ORCID iD the investigator supplied, which "
            "identifies one researcher rather than describing a person",
            ceiling=0.92,
        ),
        ConfidenceRule(
            "anchor_github_match",
            0.80,
            "The account is the exact GitHub username the investigator supplied",
            ceiling=0.88,
        ),
        ConfidenceRule(
            "anchor_website_match",
            0.60,
            "The record publishes a link to a website the investigator supplied for " "the subject",
            ceiling=0.75,
        ),
        ConfidenceRule(
            "anchor_occupation_match",
            0.20,
            "The stated occupation matches, which very many unrelated people of the "
            "same name will also share",
            ceiling=0.35,
        ),
        ConfidenceRule(
            "independent_corroboration",
            0.55,
            "A second, independently operated source published the same identifier "
            "for this record",
            ceiling=0.80,
        ),
        ConfidenceRule(
            "context_username_match",
            0.65,
            "The account handle matches one the investigator supplied as known for " "the subject",
            ceiling=0.80,
        ),
        ConfidenceRule(
            "context_affiliation_match",
            0.50,
            "The affiliation this source publishes matches one the investigator "
            "supplied, which is independent of the name itself",
            ceiling=0.70,
        ),
        ConfidenceRule(
            "context_location_match",
            0.25,
            "The place this source publishes matches the one supplied, which many "
            "unrelated people of the same name may also share",
            ceiling=0.45,
        ),
        ConfidenceRule(
            "weak_name_similarity",
            0.25,
            "The display names are similar, which is weak evidence on its own",
            ceiling=0.40,
        ),
        ConfidenceRule(
            "search_reference",
            0.40,
            "A search provider returned a page referencing both endpoints",
            ceiling=0.55,
        ),
        ConfidenceRule(
            "self_declared_membership",
            0.90,
            "The account holder made this membership public on their own profile",
        ),
        ConfidenceRule(
            "commit_authorship",
            0.85,
            "Public commit metadata attributes contributions to this account",
        ),
        ConfidenceRule(
            "archive_record",
            0.80,
            "Recorded by a third-party archive with a capture timestamp",
        ),
    )
}


class ConfidenceEngine:
    """Combines signals into a score, a band and a list of reasons."""

    def __init__(self, rules: dict[str, ConfidenceRule] | None = None) -> None:
        self.rules = dict(rules or DEFAULT_RULES)

    def rule(self, key: str) -> ConfidenceRule:
        """Look up a rule, raising ``KeyError`` for an unknown key."""
        return self.rules[key]

    def signal(self, key: str, detail: str | None = None) -> ConfidenceSignal:
        """Build a signal from a rule key, optionally with context."""
        signal = self.rules[key].signal()
        return signal.with_detail(detail) if detail else signal

    def score(self, signals: list[ConfidenceSignal]) -> ConfidenceAssessment:
        """Combine ``signals`` into an assessment.

        Independent signals are combined with a noisy-OR — each one reduces the
        remaining doubt — and the result is then clamped to the highest ceiling
        among the signals present. Weak evidence therefore accumulates, but
        never past what its own class of evidence can justify.
        """
        if not signals:
            return ConfidenceAssessment(
                score=0.0,
                strength=MatchStrength.WEAK_ASSOCIATION,
                reasons=["No supporting evidence was recorded"],
                signals=[],
            )

        remaining_doubt = 1.0
        for signal in signals:
            remaining_doubt *= 1.0 - _clamp(signal.score)
        combined = 1.0 - remaining_doubt

        ceiling = max(signal.ceiling for signal in signals)
        floor = max(_clamp(signal.score) for signal in signals)
        final = _clamp(min(max(combined, floor), ceiling))

        return ConfidenceAssessment(
            score=round(final, 4),
            strength=classify(final),
            reasons=[signal.reason for signal in signals],
            signals=[signal.key for signal in signals],
        )


def classify(score: float) -> MatchStrength:
    """Map a score onto its interpretation band."""
    if score >= LIKELY_MATCH_THRESHOLD:
        return MatchStrength.LIKELY_MATCH
    if score >= PROBABLE_MATCH_THRESHOLD:
        return MatchStrength.PROBABLE_MATCH
    if score >= POSSIBLE_MATCH_THRESHOLD:
        return MatchStrength.POSSIBLE_MATCH
    return MatchStrength.WEAK_ASSOCIATION


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


#: Process-wide engine using the default ruleset.
default_engine = ConfidenceEngine()
