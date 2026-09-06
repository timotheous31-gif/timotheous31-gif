"""How each source actually produced a record, in its own words.

Every PERSON candidate is joined to the page it came from by a relationship, and
that relationship carries a sentence explaining where the evidence came from.
Before this module existed the sentence came from one rule — the search
provider's — so a candidate discovered through ORCID's registry was reported as
"a search provider returned a page referencing both endpoints", which is simply
untrue.

The fix is to keep the *scoring* rule and replace the *sentence*. A reader has
to be able to tell an API lookup from a registry record from a page a human
picked out of their own search results, because those are not equally strong and
they fail in different ways.
"""

from __future__ import annotations

from app.correlation.confidence import ConfidenceSignal, default_engine

#: Collector name -> how that collector found the record. Written in the active
#: voice and naming the actual mechanism, so a report can be read by someone who
#: does not know the platform's internals.
SOURCE_EXPLANATIONS: dict[str, str] = {
    "github_people": "GitHub's public user search returned this public account.",
    "github": "GitHub's public API returned this account.",
    "orcid": "ORCID's public registry returned this researcher record.",
    "openalex": "OpenAlex returned this public author record.",
    "crossref": "Crossref returned a work crediting this author name.",
    "wikidata": "Wikidata returned this item, filtered to people only.",
    "reddit": "Reddit's public search returned this account.",
    "person_usernames": ("A public profile page exists for a username the investigator supplied."),
    "search": "A configured search provider returned this page for the query.",
    "manual_search_recon": (
        "The investigator imported this public search result from their own browser."
    ),
}

#: The evidence class each source produces. Reports group by this because an
#: API answer, a page fetch and a human's selection are different in kind.
SOURCE_EVIDENCE_CLASS: dict[str, str] = {
    "github_people": "api_fetched",
    "github": "api_fetched",
    "orcid": "api_fetched",
    "openalex": "api_fetched",
    "crossref": "api_fetched",
    "wikidata": "api_fetched",
    "reddit": "api_fetched",
    "search": "api_fetched",
    "person_usernames": "page_fetched",
    "manual_search_recon": "investigator_imported",
}

_FALLBACK = "The {source} collector returned this record for the searched name."


def explain_source(source: str, *, query: str | None = None, engine: str | None = None) -> str:
    """One truthful sentence about how ``source`` produced a record.

    ``query`` and ``engine`` sharpen the manual-import case, where *which*
    search a human ran is the whole provenance story.
    """
    name = (source or "").strip() or "unknown"
    if name == "manual_search_recon" and query:
        where = f" from {engine}" if engine else ""
        return (
            f"The investigator imported this public search result{where} "
            f"for the query {query!r}."
        )
    if name == "search" and query:
        return f"A configured search provider returned this page for the query {query!r}."
    return SOURCE_EXPLANATIONS.get(name, _FALLBACK.format(source=name))


def evidence_class(source: str) -> str:
    """Which class of evidence ``source`` produces."""
    return SOURCE_EVIDENCE_CLASS.get((source or "").strip(), "api_fetched")


def reference_signal(
    source: str, *, query: str | None = None, engine: str | None = None
) -> ConfidenceSignal:
    """The "this source returned this record" signal, worded for ``source``.

    The rule's score and ceiling are taken unchanged from the named rule, so the
    arithmetic is exactly what it was; only the sentence is corrected. A
    human-selected result is deliberately scored no higher than a machine one:
    an investigator choosing to keep a page says the page looked relevant, not
    that it is about the subject.
    """
    rule = default_engine.rule("search_reference")
    return ConfidenceSignal(
        key=rule.key,
        score=rule.score,
        reason=explain_source(source, query=query, engine=engine),
        ceiling=rule.ceiling,
    )
