"""Where a claim came from, as distinct from which API served it.

The defect this exists to close was measured, not theorised. ORCID and Wikidata
both publishing the same ORCID iD raised two candidates from 0.1500 to 0.6175 on
the strength of ``are_independent("orcid", "wikidata") == True`` — while
Wikidata's ORCID iDs are routinely added by bots that read them *out of ORCID*.
One fact, copied once, scored as two parties agreeing.

So three things are kept apart:

**Retrieval channel** — "Wikidata returned this". A hostname. It says who
answered the request and nothing about where the answer originated.

**Claim origin** — "Wikidata states this identifier was imported from ORCID".
Sometimes knowable per claim (Wikidata publishes reference qualifiers), otherwise
only knowable at the level of what a source is *known to ingest*.

**Independence status** — ``INDEPENDENT``, ``DEPENDENT`` or ``UNKNOWN``.

And one rule governs the rest:

    UNKNOWN INDEPENDENCE IS NOT INDEPENDENCE.

Only ``INDEPENDENT`` amplifies a score. ``DEPENDENT`` and ``UNKNOWN`` agreements
are kept and shown — an investigator wants to know two indexes carry the same
identifier — but they are described as that, never as corroboration, and they
fire no confidence rule. The cost is a missed lift when two sources really were
independent and we could not prove it. That is the right cost: a false
corroboration moves a stranger toward the subject, and it does so invisibly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Independence(StrEnum):
    """What can be established about two sources agreeing on one identifier."""

    #: Two parties that could not have taken the value from each other, or from
    #: a shared upstream. Only this amplifies a score.
    INDEPENDENT = "INDEPENDENT"
    #: One ingests the other, or both ingest a common third source, or they are
    #: the same source twice.
    DEPENDENT = "DEPENDENT"
    #: Nothing is established either way. Treated as dependent for scoring and
    #: labelled as unknown for the reader, because those are different facts.
    UNKNOWN = "UNKNOWN"


#: Only this status may raise a score.
AMPLIFYING = frozenset({Independence.INDEPENDENT})


@dataclass(frozen=True, slots=True)
class SourceLineage:
    """What one source's claim of one identifier kind rests on.

    ``authoritative`` means the source is where the identifier is issued or
    self-asserted by its subject — ORCID for ORCID iDs, Crossref for DOIs,
    GitHub for GitHub logins. ``ingests_from`` names the sources it is known to
    copy this identifier out of. A source that is neither authoritative nor a
    known ingester of the identifier is ``UNKNOWN``: we have not established
    where it got the value, and that is not the same as having established that
    it got it independently.
    """

    source: str
    identifier: str
    authoritative: bool = False
    ingests_from: frozenset[str] = field(default_factory=frozenset)
    note: str = ""


def _lineage(
    source: str,
    identifier: str,
    *,
    authoritative: bool = False,
    ingests: tuple[str, ...] = (),
    note: str = "",
) -> SourceLineage:
    return SourceLineage(
        source=source,
        identifier=identifier,
        authoritative=authoritative,
        ingests_from=frozenset(ingests),
        note=note,
    )


#: The declared lineage table, per (source, identifier kind).
#:
#: Every entry is a statement about how a public dataset is built, and each one
#: is written down with its reason rather than left to a reader to infer. Where
#: the honest answer is "this is not established", the entry is simply absent and
#: the pair resolves to UNKNOWN.
LINEAGE: tuple[SourceLineage, ...] = (
    # --- ORCID -------------------------------------------------------------
    _lineage(
        "orcid",
        "orcid",
        authoritative=True,
        note="ORCID issues the iD and its holder asserts the record.",
    ),
    _lineage(
        "orcid",
        "doi",
        ingests=("crossref",),
        note="Works on an ORCID record are largely imported from Crossref and DataCite.",
    ),
    _lineage(
        "orcid",
        "github_login",
        authoritative=True,
        note=(
            "A researcher-url on an ORCID record is asserted by the record's holder, "
            "independently of the platform it points at."
        ),
    ),
    # --- Crossref ----------------------------------------------------------
    _lineage(
        "crossref",
        "doi",
        authoritative=True,
        note="Crossref registers the DOI from the publisher's deposit.",
    ),
    # Crossref carries publisher-supplied ORCID iDs. Some are collected through
    # ORCID's own authentication and some are typed by a publisher; the API does
    # not say which. Deliberately absent, so the pair resolves to UNKNOWN.
    # --- OpenAlex ----------------------------------------------------------
    _lineage(
        "openalex",
        "doi",
        ingests=("crossref",),
        note="OpenAlex indexes Crossref metadata.",
    ),
    _lineage(
        "openalex",
        "orcid",
        ingests=("orcid", "crossref"),
        note="OpenAlex author records take ORCID iDs from ORCID and from Crossref deposits.",
    ),
    _lineage(
        "openalex",
        "openalex",
        authoritative=True,
        note="OpenAlex issues its own work and author identifiers.",
    ),
    _lineage(
        "openalex",
        "wikidata",
        ingests=("wikidata",),
        note="OpenAlex carries Wikidata identifiers taken from Wikidata.",
    ),
    # --- Wikidata ----------------------------------------------------------
    _lineage(
        "wikidata",
        "wikidata",
        authoritative=True,
        note="Wikidata issues the QID.",
    ),
    _lineage(
        "wikidata",
        "orcid",
        ingests=("orcid",),
        note=(
            "P496 values are routinely added by bots and editors reading ORCID. A "
            "claim's own references can confirm this per statement."
        ),
    ),
    _lineage(
        "wikidata",
        "doi",
        ingests=("crossref",),
        note="P356 values are imported from Crossref by bots.",
    ),
    _lineage(
        "wikidata",
        "github_login",
        ingests=("github", "github_people"),
        note="P2037 is added by an editor reading the GitHub profile.",
    ),
    # --- GitHub ------------------------------------------------------------
    _lineage(
        "github",
        "github_login",
        authoritative=True,
        note="GitHub issues the login.",
    ),
    _lineage(
        "github_people",
        "github_login",
        authoritative=True,
        ingests=("github",),
        note="The same GitHub API as the `github` collector, so never independent of it.",
    ),
    # --- Retrieval channels that originate nothing -------------------------
    # provider_search and manual_search_recon are channels over third-party
    # pages. A page can copy another page, and an index can carry both. They are
    # deliberately absent from this table: every agreement involving them is
    # UNKNOWN, which is the truth.
)

_BY_KEY: dict[tuple[str, str], SourceLineage] = {
    (item.source, item.identifier): item for item in LINEAGE
}

#: Sources that only relay other people's pages. Named so a reason can say so.
RELAY_SOURCES: frozenset[str] = frozenset(
    {"provider_search", "manual_search_recon", "search", "http_meta", "wayback"}
)


def lineage_for(source: str, identifier: str) -> SourceLineage | None:
    """The declared lineage for one source's claim of one identifier kind."""
    return _BY_KEY.get((source, identifier))


@dataclass(frozen=True, slots=True)
class Verdict:
    """An independence ruling, and the sentence that explains it."""

    status: Independence
    reason: str

    @property
    def amplifies(self) -> bool:
        return self.status in AMPLIFYING


def _observed_upstreams(claim_lineage: dict[str, object] | None) -> frozenset[str]:
    """Sources a *specific* claim says it was imported from.

    Per-claim evidence outranks the table: a Wikidata statement whose reference
    says "imported from ORCID" settles the question for that statement, whatever
    the general pattern is.
    """
    if not isinstance(claim_lineage, dict):
        return frozenset()
    raw = claim_lineage.get("imported_from")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item).strip().lower() for item in raw if str(item).strip())


def independence(
    first: str,
    second: str,
    identifier: str,
    *,
    first_claim: dict[str, object] | None = None,
    second_claim: dict[str, object] | None = None,
) -> Verdict:
    """Rule on two sources publishing the same value for one identifier kind.

    ``first_claim`` / ``second_claim`` carry per-claim provenance when a source
    publishes any — Wikidata's reference qualifiers, for instance. They can only
    make a verdict *more* conservative, never less.
    """
    if first == second:
        return Verdict(
            Independence.DEPENDENT,
            f"Both records came from {first}, so this is one source repeating itself.",
        )

    left = lineage_for(first, identifier)
    right = lineage_for(second, identifier)

    # 1. Per-claim provenance, where a source published any.
    stated = _observed_upstreams(first_claim) | _observed_upstreams(second_claim)
    if second in stated or first in stated:
        return Verdict(
            Independence.DEPENDENT,
            f"{first} and {second} publish the same {identifier.upper()}, and the claim "
            f"itself records that it was imported from the other.",
        )

    # 2. One ingests the other.
    if right is not None and first in right.ingests_from:
        return Verdict(
            Independence.DEPENDENT,
            f"{second} takes {identifier.upper()} values from {first}, so their agreement "
            f"may be one value copied once. {right.note}",
        )
    if left is not None and second in left.ingests_from:
        return Verdict(
            Independence.DEPENDENT,
            f"{first} takes {identifier.upper()} values from {second}, so their agreement "
            f"may be one value copied once. {left.note}",
        )

    # 3. Both ingest a common upstream.
    if left is not None and right is not None:
        shared = left.ingests_from & right.ingests_from
        if shared:
            names = ", ".join(sorted(shared))
            return Verdict(
                Independence.DEPENDENT,
                f"{first} and {second} both take {identifier.upper()} values from {names}, "
                f"so they may be reporting one upstream value twice.",
            )

    # 4. A relay channel originates nothing, so it can never be a second party.
    for relay in (first, second):
        if relay in RELAY_SOURCES:
            other = second if relay is first else first
            return Verdict(
                Independence.UNKNOWN,
                f"{relay} relays third-party pages rather than publishing records of its "
                f"own, so whether it agrees with {other} independently cannot be "
                f"established from the result alone.",
            )

    # 5. Both authoritative for this identifier, neither ingesting the other.
    if left is not None and right is not None and left.authoritative and right.authoritative:
        return Verdict(
            Independence.INDEPENDENT,
            f"{first} and {second} each publish {identifier.upper()} on their own authority "
            f"and neither takes it from the other. {left.note} {right.note}".strip(),
        )

    # 6. Anything else is unestablished, and unestablished is not independent.
    missing = [name for name, item in ((first, left), (second, right)) if item is None]
    detail = (
        f"No lineage is recorded for {', '.join(missing)} on {identifier.upper()}"
        if missing
        else f"Neither {first} nor {second} is the source of record for {identifier.upper()}"
    )
    return Verdict(
        Independence.UNKNOWN,
        f"{detail}, so their agreement cannot be established as independent. "
        f"Recorded as a shared identifier, not as corroboration.",
    )


#: How each status is described wherever a human reads it.
STATUS_LABELS: dict[Independence, str] = {
    Independence.INDEPENDENT: "Independently corroborated",
    Independence.DEPENDENT: "Same value in two indexes (one may be copied from the other)",
    Independence.UNKNOWN: "Same value in two indexes (independence not established)",
}

#: What each status does to a score. Printed in the report so the reader does
#: not have to take it on trust.
STATUS_EFFECT: dict[Independence, str] = {
    Independence.INDEPENDENT: "Raises confidence through the independent-corroboration rule.",
    Independence.DEPENDENT: "No effect on confidence. Shown as a lead to verify.",
    Independence.UNKNOWN: "No effect on confidence. Shown as a lead to verify.",
}
