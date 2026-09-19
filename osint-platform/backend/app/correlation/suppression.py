"""Which candidates an investigator sees first, and which fold away.

A name search returns strangers. Searching "Sara Samara" finds every Sara
Samara, every Sara Samari, and — through the name-variant engine, which is doing
its job — records that share only part of the name. Each one is scored honestly
at 0.05, each one's explanation says plainly that nothing corroborates it, and
each one still arrives in the analyst's list looking like a lead.

That is the problem this module solves, and it is worth being precise about what
it is *not*:

* **It is not scoring.** Nothing here changes a score, and nothing here is a
  reason to change one. A candidate suppressed by this module has exactly the
  correlation score it had before, computed by exactly the same rules.
* **It is not deletion.** Every candidate, every piece of evidence and every
  provenance row stays where it was. Suppression decides the *order and
  prominence* of a list, and nothing else. The exported evidence is unchanged.
* **It is not a second opinion about identity.** The test is mechanical: does
  anything other than the name connect this record to the subject?

The rule
--------

A candidate is shown in the primary view when **either**

* it carries at least one non-name corroborating anchor — an employer, a school,
  an occupation, a place, a username, an ORCID, a GitHub account, a website or a
  profile URL the investigator supplied — **or**
* its correlation score is above the suppression threshold, meaning something
  beyond a bare name contributed to it.

Otherwise it folds into a collapsed section that says how many are in there.

An analyst's judgement outranks both. A confirmed candidate is always in the
primary view whatever it scored, because a human looked at it and said so; a
rejected one is always out of it, for the same reason. That is the one place in
this file where a decision made by a person overrides a number, and it is
deliberate: the automated score and the human verdict are stored apart precisely
so the human can win.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.settings import DEFAULT_SUPPRESSION_THRESHOLD
from app.correlation.anchors import ANCHOR_RULES
from app.models.enums import AnalystDecision

#: Shown normally: corroborated, or scored above the threshold, or confirmed.
PRIMARY = "PRIMARY"
#: Folded into the collapsed section. Stored, exported and reviewable as before.
LOW_CONFIDENCE = "LOW_CONFIDENCE"
#: An analyst ruled this out. Out of the primary view, kept for the audit trail.
REJECTED = "REJECTED"

#: The anchor kinds that count as corroboration, taken from the scoring layer
#: rather than restated here.
#:
#: Restating them would create exactly the drift this module exists to avoid: a
#: new anchor kind would start contributing to a score while still counting as
#: "name-only" for presentation, and a corroborated candidate would hide itself.
#: Every kind in ``ANCHOR_RULES`` is by definition something other than the name.
CORROBORATING_ANCHORS: frozenset[str] = frozenset(ANCHOR_RULES)

#: Above this, something other than a bare name contributed to the score.
#:
#: Chosen from the rule table rather than picked round. The name rules run
#: 0.15 (exact spelling), 0.14 (hyphenation), 0.12 (an initial), 0.08 (a part
#: dropped), 0.05 (only some parts shared); the weakest anchor is 0.20. So 0.10
#: sits in a real gap in the weights, and it separates *a source that publishes
#: the whole name* from *a source that publishes a truncated or partial one*.
#:
#: That boundary is the honest one. "Sara Samari" for a search of "Sara Samara"
#: shares part of a name and nothing else; it is a lead, not a candidate. An
#: exact-name record is still not identification — the rule text says so — but it
#: is the strongest thing a name alone can produce, and hiding it by default
#: would be hiding the search's actual result.
#:
#: Being in a gap matters: a small change to any name weight cannot silently
#: move candidates across the line.
#:
#: Zero is special-cased to mean *off*, not "a threshold of zero" — see
#: :func:`classify_candidate`.
#:
#: The number itself is defined in :mod:`app.core.settings` and re-exported here
#: so this module reads as the one place the choice is explained, without two
#: copies of the value existing.
__all__ = [
    "CORROBORATING_ANCHORS",
    "DEFAULT_SUPPRESSION_THRESHOLD",
    "LOW_CONFIDENCE",
    "PRIMARY",
    "REJECTED",
    "Visibility",
    "classify_candidate",
    "partition",
]


@dataclass(frozen=True, slots=True)
class Visibility:
    """Where a candidate belongs, and the sentence explaining why."""

    presentation: str
    reason: str

    @property
    def suppressed(self) -> bool:
        """True when this candidate is kept out of the primary view."""
        return self.presentation != PRIMARY

    def to_dict(self) -> dict[str, str]:
        return {"presentation": self.presentation, "reason": self.reason}


def classify_candidate(
    *,
    score: float,
    corroborated_by: list[str] | tuple[str, ...] | None,
    decision: str | None = None,
    threshold: float = DEFAULT_SUPPRESSION_THRESHOLD,
    anchors_supplied: bool = True,
) -> Visibility:
    """Decide where one candidate belongs in the presentation.

    Args:
        score: the candidate's correlation score, unchanged and unread except
            for this comparison.
        corroborated_by: anchor kinds that matched, as the assessment recorded
            them. Any one of them is enough.
        decision: the analyst's verdict, if they recorded one. Outranks the rest.
        threshold: the score above which a candidate stands on its own.
        anchors_supplied: whether the investigator supplied any anchors at all.
            When they did not, corroboration was never *possible*, so its absence
            says nothing about the candidate — see below.

    Returns:
        A :class:`Visibility` carrying the verdict and the reason for it.
    """
    if decision == AnalystDecision.CONFIRMED:
        return Visibility(
            PRIMARY,
            "An analyst confirmed this candidate, so it is shown whatever it scored.",
        )
    if decision == AnalystDecision.REJECTED:
        return Visibility(
            REJECTED,
            "An analyst ruled this candidate out. It is kept for the audit trail "
            "and left out of the primary view.",
        )

    if threshold <= 0:
        # Suppression off. Written as its own branch rather than left to
        # `score > threshold`, because that comparison is false for a candidate
        # scoring exactly 0.0 — so a zero threshold would still have suppressed
        # something, and "0 turns this off" would have been very nearly true.
        #
        # Nothing in the model forbids a 0.0 candidate: `Entity.confidence` is a
        # plain float column with no CHECK constraint. The scoring pipeline
        # cannot currently produce one — every candidate carries at least one
        # name rule, the weakest is 0.05, and the engine floors a result at the
        # highest signal it combined — but that is an invariant spanning
        # extraction, the engine, the resolver and the corroboration pass, and
        # no single place enforces it. An off switch should not depend on it.
        return Visibility(
            PRIMARY,
            "Suppression is disabled (threshold 0), so every candidate is shown.",
        )

    anchors = [kind for kind in (corroborated_by or []) if kind in CORROBORATING_ANCHORS]
    if anchors:
        return Visibility(
            PRIMARY,
            "Something other than the name connects this record to the subject: "
            + ", ".join(sorted(anchors))
            + ".",
        )

    if score > threshold:
        return Visibility(
            PRIMARY,
            f"No anchor matched, but the correlation score ({score:.2f}) is above "
            f"the {threshold:.2f} threshold.",
        )

    if not anchors_supplied:
        # Nothing could have corroborated this, because there was nothing to
        # corroborate it against. Folding it away on that basis would report the
        # investigator's own missing input as a property of the record.
        return Visibility(
            LOW_CONFIDENCE,
            "Only the name connects this record to the subject, and no anchors "
            "were supplied for it to be checked against.",
        )

    return Visibility(
        LOW_CONFIDENCE,
        f"Only the name connects this record to the subject, and at {score:.2f} "
        f"the score is at or below the {threshold:.2f} threshold. Name similarity "
        "is a lead to check, not evidence of identity.",
    )


def partition(
    candidates: list[tuple[object, Visibility]],
) -> tuple[list[object], list[object], list[object]]:
    """Split ``(candidate, verdict)`` pairs into primary, low-confidence, rejected.

    The order within each group is the order given, so a caller's ranking — most
    corroborated first — survives the split.
    """
    primary = [item for item, verdict in candidates if verdict.presentation == PRIMARY]
    low = [item for item, verdict in candidates if verdict.presentation == LOW_CONFIDENCE]
    rejected = [item for item, verdict in candidates if verdict.presentation == REJECTED]
    return primary, low, rejected
