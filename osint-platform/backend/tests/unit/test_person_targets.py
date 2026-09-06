"""PERSON targets: explicit classification, narrow collection, separate candidates.

Three properties are load-bearing here and each has its own section:

1. A bare name is never *inferred* to be anything. The platform asks.
2. A PERSON target schedules only collectors that make sense for a name — in
   particular, no infrastructure lookup is pointed at a person.
3. Two results that share a name stay two candidates. Nothing in the scoring
   path can fuse them without independent evidence.
"""

from __future__ import annotations

import uuid

import pytest

from app.collectors.registry import load_builtin_collectors, plan_collectors
from app.collectors.search import SearchCollector
from app.core.errors import AmbiguousTargetError, ValidationError
from app.core.settings import Settings
from app.correlation.confidence import AUTO_MERGE_THRESHOLD, default_engine
from app.correlation.extraction import extract
from app.models.enums import EntityType, FindingKind, RelationshipType, TargetType
from app.services.normalization import detect_type, normalize_target
from app.services.providers.search import SearchResult


class _Finding:
    """The subset of a Finding row that extraction reads."""

    def __init__(self, kind, data, collector="search", source_url=None, confidence=0.2):
        self.id = uuid.uuid4()
        self.kind = kind
        self.data = data
        self.collector = collector
        self.source_url = source_url
        self.confidence = confidence


def _person_finding(url: str, name: str = "Timotheous Samar", **extra):
    data = {
        "url": url,
        "host": url.split("/")[2],
        "title": extra.pop("title", f"Page about {name}"),
        "snippet": "",
        "rank": extra.pop("rank", 1),
        "query": f'"{name}"',
        "provider": "brave",
        "subject_name": name,
        "subject_value": name.lower(),
        "candidate_key": url,
    }
    data.update(extra)
    return _Finding(FindingKind.PERSON_CANDIDATE, data, source_url=url)


# ------------------------------------------------- 1. no guessing on free text


@pytest.mark.parametrize(
    "raw",
    [
        "Timotheous Samar",
        "Example Corporation",
        "The Example Institute of Technology",
        "Jane Q. Public",
    ],
)
def test_free_text_is_never_inferred(raw):
    """The exact bug reported from Docker: a full name became an ORGANIZATION."""
    with pytest.raises(AmbiguousTargetError) as caught:
        detect_type(raw)
    assert caught.value.detail == {"candidates": ["PERSON", "ORGANIZATION"]}
    assert caught.value.code == "ambiguous_target_type"


def test_normalize_target_propagates_the_ambiguity():
    with pytest.raises(AmbiguousTargetError):
        normalize_target("Timotheous Samar")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", TargetType.DOMAIN),
        ("@exampleuser", TargetType.USERNAME),
        ("user@example.com", TargetType.EMAIL),
        ("https://example.com/page", TargetType.URL),
        ("203.0.113.7", TargetType.IP),
        ("octocat/Hello-World", TargetType.REPOSITORY),
        ("https://reddit.com/u/exampleuser", TargetType.SOCIAL_PROFILE),
    ],
)
def test_structured_input_still_infers(raw, expected):
    """Removing the organisation guess must not disturb real inference."""
    assert detect_type(raw) is expected
    assert normalize_target(raw).type is expected


def test_explicit_person_selection_is_accepted():
    target = normalize_target("Timotheous Samar", TargetType.PERSON)
    assert target.type is TargetType.PERSON
    assert target.value == "timotheous samar"
    assert target.attributes["display_name"] == "Timotheous Samar"
    assert target.attributes["is_identifier"] is False


def test_explicit_organization_selection_is_accepted():
    target = normalize_target("Timotheous Samar", TargetType.ORGANIZATION)
    assert target.type is TargetType.ORGANIZATION


def test_person_canonicalisation_folds_only_case_and_spacing():
    spaced = normalize_target("  Timotheous   Samar ", TargetType.PERSON)
    plain = normalize_target("timotheous samar", TargetType.PERSON)
    assert spaced.value == plain.value
    # …but the original spelling survives for display and for search queries.
    assert spaced.attributes["display_name"] == "Timotheous Samar"


def test_person_canonicalisation_keeps_distinct_names_distinct():
    """No initial expansion, no nickname folding: those merge different people."""
    assert (
        normalize_target("Tim Samar", TargetType.PERSON).value
        != normalize_target("Timotheous Samar", TargetType.PERSON).value
    )
    assert (
        normalize_target("T. Samar", TargetType.PERSON).value
        != normalize_target("Timotheous Samar", TargetType.PERSON).value
    )


def test_person_rejects_an_empty_name():
    with pytest.raises(ValidationError):
        normalize_target("x", TargetType.PERSON)


# ------------------------------------------------- 2. collector applicability


#: Every PERSON collector that needs no API key. The platform must stay usable
#: at zero cost, so this set is asserted exactly rather than loosely.
FREE_PERSON_COLLECTORS = {
    "crossref",
    "github_people",
    "openalex",
    "orcid",
    "person_usernames",
    "reddit",
    "wikidata",
}


def test_person_targets_schedule_only_name_appropriate_collectors():
    load_builtin_collectors()
    planned = {collector.name for collector in plan_collectors(TargetType.PERSON)}
    # The free sources, plus the optional paid search collector.
    assert planned == FREE_PERSON_COLLECTORS | {"search"}


@pytest.mark.parametrize("infrastructure", ["dns", "rdap", "ctlog", "http_meta", "wayback"])
def test_infrastructure_collectors_never_accept_a_person(infrastructure):
    """A person's name is not a hostname; resolving one would be nonsense."""
    load_builtin_collectors()
    planned = {collector.name for collector in plan_collectors(TargetType.PERSON)}
    assert infrastructure not in planned


def test_github_does_not_guess_a_login_from_a_persons_name():
    load_builtin_collectors()
    planned = {collector.name for collector in plan_collectors(TargetType.PERSON)}
    assert "github" not in planned


def test_person_search_runs_exactly_one_unqualified_query():
    collector = SearchCollector(Settings(_env_file=None))
    target = normalize_target("Timotheous Samar", TargetType.PERSON)
    queries = collector.queries(target)
    assert queries == ['"Timotheous Samar"']


def test_person_queries_never_add_personal_qualifiers():
    collector = SearchCollector(Settings(_env_file=None))
    target = normalize_target("Timotheous Samar", TargetType.PERSON)
    joined = " ".join(collector.queries(target)).lower()
    for term in (
        "address",
        "phone",
        "home",
        "resume",
        "cv",
        "family",
        "employer",
        "salary",
        "arrest",
        "dob",
    ):
        assert term not in joined


def test_person_search_results_are_candidates_not_plain_hits():
    collector = SearchCollector(Settings(_env_file=None))
    target = normalize_target("Timotheous Samar", TargetType.PERSON)
    item = SearchResult(
        title="Example profile",
        url="https://example.com/people/1",
        snippet="",
        rank=1,
        provider="brave",
    )
    draft = collector._normalize_result(item, '"Timotheous Samar"', target)[0]
    assert draft.kind is FindingKind.PERSON_CANDIDATE
    assert draft.data["subject_value"] == "timotheous samar"
    # Below POSSIBLE_MATCH (0.50): a name match is not an identification, no
    # matter how highly the provider ranked the page.
    assert draft.confidence < 0.5
    assert any("names are not unique" in reason for reason in draft.confidence_reasons)


def test_non_person_search_results_are_unchanged():
    """The person branch must not alter how other target types are recorded."""
    collector = SearchCollector(Settings(_env_file=None))
    target = normalize_target("example.com")
    item = SearchResult(
        title="Example", url="https://example.org/a", snippet="", rank=1, provider="brave"
    )
    draft = collector._normalize_result(item, '"example.com"', target)[0]
    assert draft.kind is FindingKind.SEARCH_RESULT


# ------------------------------------------------- 3. same-name separation


def test_same_name_results_stay_separate_candidates():
    """Two pages about two different people who share a name: two candidates."""
    findings = [
        _person_finding("https://example.com/alice"),
        _person_finding("https://example.org/someone-else"),
    ]
    result = extract(findings)

    personas = [key for key in result.entities if key[0] is EntityType.PERSONA]
    candidates = [key for key in personas if key[1].startswith("person-candidate:")]
    subjects = [key for key in personas if key[1].startswith("person:")]

    assert len(candidates) == 2, "same-name pages must not collapse into one persona"
    assert len(subjects) == 1, "the case still investigates a single subject"
    assert {key[1] for key in candidates} == {
        "person-candidate:https://example.com/alice",
        "person-candidate:https://example.org/someone-else",
    }


def test_candidates_are_never_linked_to_each_other():
    findings = [
        _person_finding("https://example.com/alice"),
        _person_finding("https://example.org/someone-else"),
    ]
    result = extract(findings)
    candidate_keys = {key for key in result.entities if key[1].startswith("person-candidate:")}
    for source, target, _kind in result.relationships:
        assert not (
            source in candidate_keys and target in candidate_keys
        ), "two same-name candidates were linked to each other"


def test_subject_to_candidate_link_is_labelled_as_name_only():
    result = extract([_person_finding("https://example.com/alice")])
    edge = next(
        edge
        for edge in result.relationships.values()
        if edge.type is RelationshipType.POSSIBLY_SAME_ENTITY
    )
    assert edge.attributes["basis"] == "name_match_only"
    assert edge.attributes["requires_corroboration"] is True
    assert [signal.key for signal in edge.signals] == ["same_person_name"]


def test_name_matches_can_never_reach_the_auto_merge_threshold():
    """The property that keeps same-name people apart, stated directly.

    Even fifty pages all naming the same person cannot push a name-only
    association up to the score at which the resolver would merge entities.
    """
    signal = default_engine.signal("same_person_name")
    assessment = default_engine.score([signal] * 50)
    assert assessment.score <= 0.30
    assert assessment.score < AUTO_MERGE_THRESHOLD
    assert not assessment.auto_mergeable


def test_candidate_carries_its_provenance():
    url = "https://example.com/alice"
    finding = _person_finding(url, title="Alice at Example")
    result = extract([finding])

    candidate = next(
        entity for key, entity in result.entities.items() if key[1].startswith("person-candidate:")
    )
    assert candidate.attributes["reference_url"] == url
    assert candidate.attributes["reference_title"] == "Alice at Example"
    assert candidate.attributes["identity_established"] is False
    assert finding.id in candidate.source_finding_ids

    reference = next(
        edge
        for edge in result.relationships.values()
        if edge.type is RelationshipType.REFERENCED_BY
    )
    assert reference.evidence_finding_ids == [finding.id]
    assert reference.attributes["provider"] == "brave"


def test_a_malformed_candidate_is_skipped_not_fatal():
    """A finding missing its subject cannot silently become a nameless node."""
    broken = _Finding(FindingKind.PERSON_CANDIDATE, {"url": "https://example.com/a"})
    result = extract([broken, _person_finding("https://example.com/alice")])
    assert not any(key[1] == "person-candidate:https://example.com/a" for key in result.entities)
    assert any(key[1] == "person-candidate:https://example.com/alice" for key in result.entities)
