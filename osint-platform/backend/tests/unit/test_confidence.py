"""Confidence scoring rules and combination."""

from __future__ import annotations

import pytest

from app.correlation.confidence import (
    AUTO_MERGE_THRESHOLD,
    DEFAULT_RULES,
    ConfidenceEngine,
    ConfidenceRule,
    classify,
    default_engine,
)
from app.models.enums import MatchStrength


@pytest.fixture
def engine():
    return ConfidenceEngine()


def test_documented_rule_scores(engine):
    assert engine.rule("verified_link").score == 0.95
    assert engine.rule("site_links_profile").score == 0.90
    assert engine.rule("same_unique_username").score == 0.50
    assert engine.rule("same_username_matching_bio").score == 0.70
    assert engine.rule("same_username_shared_website").score == 0.85
    assert engine.rule("weak_name_similarity").score == 0.25


def test_every_rule_carries_a_reason():
    assert all(rule.reason.strip() for rule in DEFAULT_RULES.values())
    assert all(0.0 <= rule.score <= 1.0 for rule in DEFAULT_RULES.values())


def test_single_signal_scores_at_its_rule_value(engine):
    assessment = engine.score([engine.signal("same_unique_username")])
    assert assessment.score == 0.5
    assert assessment.strength is MatchStrength.POSSIBLE_MATCH
    assert assessment.reasons == [DEFAULT_RULES["same_unique_username"].reason]
    assert assessment.signals == ["same_unique_username"]


def test_no_signals_scores_zero(engine):
    assessment = engine.score([])
    assert assessment.score == 0.0
    assert assessment.strength is MatchStrength.WEAK_ASSOCIATION
    assert assessment.reasons


def test_independent_signals_accumulate(engine):
    single = engine.score([engine.signal("search_reference")]).score
    double = engine.score(
        [engine.signal("search_reference"), engine.signal("weak_name_similarity")]
    ).score
    assert double > single


def test_username_evidence_cannot_reach_certainty(engine):
    """No number of username matches may rival a self-published link."""
    many = engine.score([engine.signal("same_unique_username") for _ in range(20)])
    assert many.score <= DEFAULT_RULES["same_unique_username"].ceiling
    assert many.score < engine.score([engine.signal("site_links_profile")]).score
    assert not many.auto_mergeable


def test_common_username_is_capped_lower(engine):
    common = engine.score([engine.signal("same_common_username") for _ in range(10)])
    assert common.score <= 0.40
    assert common.strength is MatchStrength.WEAK_ASSOCIATION


def test_shared_infrastructure_is_capped(engine):
    assessment = engine.score([engine.signal("shared_infrastructure") for _ in range(5)])
    assert assessment.score <= 0.65


def test_strong_signal_lifts_the_ceiling(engine):
    assessment = engine.score(
        [engine.signal("site_links_profile"), engine.signal("same_unique_username")]
    )
    assert assessment.score >= 0.90
    assert assessment.strength is MatchStrength.LIKELY_MATCH


def test_reciprocal_links_permit_a_merge_suggestion(engine):
    assessment = engine.score(
        [engine.signal("site_links_profile"), engine.signal("profile_links_site")]
    )
    assert assessment.score >= AUTO_MERGE_THRESHOLD
    assert assessment.auto_mergeable


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, MatchStrength.LIKELY_MATCH),
        (0.95, MatchStrength.LIKELY_MATCH),
        (0.90, MatchStrength.LIKELY_MATCH),
        (0.89, MatchStrength.PROBABLE_MATCH),
        (0.70, MatchStrength.PROBABLE_MATCH),
        (0.69, MatchStrength.POSSIBLE_MATCH),
        (0.50, MatchStrength.POSSIBLE_MATCH),
        (0.49, MatchStrength.WEAK_ASSOCIATION),
        (0.0, MatchStrength.WEAK_ASSOCIATION),
    ],
)
def test_banding_matches_the_documented_thresholds(score, expected):
    assert classify(score) is expected


def test_signal_detail_is_appended_to_the_reason(engine):
    signal = engine.signal("same_unique_username", "handle 'exampleuser'")
    assert "exampleuser" in signal.reason
    assert signal.key == "same_unique_username"


def test_ruleset_is_configurable():
    custom = ConfidenceEngine(
        {"house_rule": ConfidenceRule("house_rule", 0.42, "Because the policy says so")}
    )
    assessment = custom.score([custom.signal("house_rule")])
    assert assessment.score == 0.42
    assert assessment.reasons == ["Because the policy says so"]


def test_unknown_rule_raises(engine):
    with pytest.raises(KeyError):
        engine.signal("no_such_rule")


def test_default_engine_is_shared():
    assert default_engine.rule("dns_resolution").score == 0.95
