"""Name variants: bounded, deterministic, and never an identity claim.

The gap these close is a real one. An investigation for "Tabitha Afzal Imdad"
found nothing, because public sources write her as "Tabitha Afzal". Searching
only the canonical spelling is too literal to be useful — and generating
spellings is only safe if each one is produced by a stated rule and scored below
the exact match.
"""

from __future__ import annotations

import pytest

from app.correlation.confidence import default_engine
from app.services.name_variants import (
    EXACT,
    EXTENDED,
    HYPHENATION,
    INITIAL,
    MAX_PARTS_FOR_VARIANTS,
    MAX_VARIANTS,
    PARTIAL,
    REDUCED,
    VARIANT_NAME_WEIGHT,
    VARIANT_ORDER,
    classify_observed_name,
    confidence_rule_for,
    fold_name,
    generate_variants,
    name_parts,
)

CANONICAL = "Tabitha Afzal Imdad"


def _values(name: str) -> list[str]:
    return [variant.value for variant in generate_variants(name)]


def _kinds(name: str) -> dict[str, str]:
    return {variant.value: variant.variant_type for variant in generate_variants(name)}


# ------------------------------------------------------ the acceptance name


def test_the_three_part_canonical_name_yields_every_required_spelling():
    """Exactly the set the acceptance brief names."""
    produced = _values(CANONICAL)
    for required in (
        "Tabitha Afzal Imdad",
        "Tabitha Afzal",
        "Tabitha Imdad",
        "Tabitha A Imdad",
        "Tabitha A. Imdad",
    ):
        assert required in produced, f"{required!r} was not generated"


def test_each_spelling_records_the_rule_that_produced_it():
    for variant in generate_variants(CANONICAL):
        assert variant.variant_generation_reason if False else variant.reason
        assert variant.canonical == CANONICAL
        assert variant.variant_type in VARIANT_ORDER
        payload = variant.to_dict()
        assert payload["canonical_target"] == CANONICAL
        assert payload["variant_generation_reason"]
        assert payload["search_variant"] == variant.value


def test_the_canonical_name_is_the_first_variant_and_is_never_rewritten():
    variants = generate_variants(CANONICAL)
    assert variants[0].value == CANONICAL
    assert variants[0].variant_type == EXACT
    # Every variant still names the canonical target it came from.
    assert {variant.canonical for variant in variants} == {CANONICAL}


def test_reduced_and_initial_variants_are_typed_correctly():
    kinds = _kinds(CANONICAL)
    assert kinds["Tabitha Afzal"] == REDUCED
    assert kinds["Tabitha Imdad"] == REDUCED
    assert kinds["Tabitha A Imdad"] == INITIAL
    assert kinds["Tabitha A. Imdad"] == INITIAL
    assert kinds["Tabitha Afzal-Imdad"] == HYPHENATION


# -------------------------------------------------------------- edge cases


def test_a_two_part_name_yields_only_itself():
    """There is no middle to drop, and one token matches far too much."""
    assert _values("Tabitha Afzal") == ["Tabitha Afzal"]


def test_a_single_token_name_yields_only_itself():
    assert _values("Cher") == ["Cher"]
    assert _values("timotheous31-gif") == ["timotheous31-gif"]


def test_a_four_part_name_still_keeps_first_and_last():
    variants = generate_variants("Ana Maria Gomez Ruiz")
    for variant in variants[1:]:
        if variant.variant_type is REDUCED:
            assert variant.value.startswith("Ana")
            assert variant.value.endswith("Ruiz")


def test_a_name_longer_than_the_bound_is_searched_only_as_written():
    """Dropping parts from a five-part name invents people."""
    long_name = "One Two Three Four Five"
    assert len(name_parts(long_name)) > MAX_PARTS_FOR_VARIANTS
    assert _values(long_name) == [long_name]


def test_particles_are_never_dropped_as_if_they_were_names():
    assert name_parts("Anna van der Berg") == ["Anna", "van der Berg"]
    assert _values("Anna van der Berg") == ["Anna van der Berg"]
    assert not any(value.endswith(" van") for value in _values("Anna van der Berg"))


def test_an_existing_hyphen_is_opened_out():
    variants = _kinds("Mary-Jane Watson Parker")
    assert variants["Mary Jane Watson Parker"] == HYPHENATION


def test_whitespace_and_punctuation_are_normalised_not_searched():
    assert _values("  Tabitha   Afzal  Imdad  ")[0] == CANONICAL


def test_unicode_names_survive_intact():
    variants = _values("Ana María Gómez")
    assert variants[0] == "Ana María Gómez"
    # Folding is for comparison only; the accented spelling is what is searched.
    assert fold_name("Ana María Gómez") == "ana maria gomez"


def test_an_empty_name_produces_nothing():
    assert generate_variants("") == []
    assert generate_variants("   ") == []


@pytest.mark.parametrize(
    "name",
    [CANONICAL, "Ana María Gómez Ruiz", "Mary-Jane Watson Parker", "A B C D"],
)
def test_variant_count_is_always_bounded(name):
    assert len(generate_variants(name)) <= MAX_VARIANTS


def test_generation_is_deterministic():
    assert [v.value for v in generate_variants(CANONICAL)] == [
        v.value for v in generate_variants(CANONICAL)
    ]


def test_no_nonsensical_permutation_is_produced():
    """First and last names are never reordered, and nothing is invented."""
    produced = set(_values(CANONICAL))
    for nonsense in ("Imdad Tabitha", "Afzal Tabitha Imdad", "Imdad Afzal", "Afzal Imdad"):
        assert nonsense not in produced


# ------------------------------------------ a variant is not identity proof


def test_a_reduced_name_scores_below_an_exact_one():
    exact = default_engine.signal(confidence_rule_for(EXACT))
    reduced = default_engine.signal(confidence_rule_for(REDUCED))
    assert reduced.score < exact.score
    assert reduced.ceiling < exact.ceiling


def test_every_variant_kind_is_weaker_than_or_equal_to_exact():
    for kind in VARIANT_ORDER:
        assert VARIANT_NAME_WEIGHT[kind] <= VARIANT_NAME_WEIGHT[EXACT]
        signal = default_engine.signal(confidence_rule_for(kind))
        assert signal.ceiling <= default_engine.signal(confidence_rule_for(EXACT)).ceiling


def test_no_name_rule_can_reach_a_merge_on_its_own():
    """A name is not an identifier, whichever spelling matched."""
    from app.correlation.resolver import AUTO_MERGE_THRESHOLD

    for kind in VARIANT_ORDER:
        signal = default_engine.signal(confidence_rule_for(kind))
        assert signal.ceiling < AUTO_MERGE_THRESHOLD


# ------------------------------------------------- classifying what a source says


@pytest.mark.parametrize(
    "observed,expected",
    [
        ("Tabitha Afzal Imdad", EXACT),
        ("tabitha afzal imdad", EXACT),
        ("Tabitha Afzal-Imdad", HYPHENATION),
        ("Tabitha A. Imdad", INITIAL),
        ("Tabitha A Imdad", INITIAL),
        ("Tabitha Afzal", REDUCED),
        ("Tabitha Imdad", REDUCED),
        ("Tabitha Afzal Imdad Khan", EXTENDED),
        ("Tabitha Khan", PARTIAL),
        ("Sarah Jones", PARTIAL),
        ("", PARTIAL),
    ],
)
def test_an_observed_name_is_classified_against_the_canonical_one(observed, expected):
    kind, reason = classify_observed_name(observed, CANONICAL)
    assert kind == expected
    assert reason


def test_a_reduced_classification_says_it_needs_corroboration():
    _kind, reason = classify_observed_name("Tabitha Afzal", CANONICAL)
    assert "lead" in reason.lower() or "more people" in reason.lower()


def test_a_fuller_name_is_not_penalised_for_carrying_more():
    """Every searched part is present, so nothing searched for is missing."""
    kind, _reason = classify_observed_name("Tabitha Afzal Imdad Khan", CANONICAL)
    assert confidence_rule_for(kind) == confidence_rule_for(EXACT)
