"""The capability registry, and the boundaries it exists to state.

The registry answers one question — what may this codebase legitimately do with
platform X? — so that the answer is not spread across a dozen ``if`` statements
that can disagree. Most of these tests are about the disagreements it prevents.
"""

from __future__ import annotations

import pytest

from app.collectors.capabilities import (
    _EXTRA,
    CAPABILITIES,
    capability_for,
    handle_checkable,
    reference_only,
    search_platforms,
    summarise,
)
from app.collectors.social import PLATFORMS as URL_PLATFORMS
from app.collectors.social import classify_url

MAINSTREAM = (
    "linkedin",
    "instagram",
    "facebook",
    "youtube",
    "twitter",
    "tiktok",
    "snapchat",
    "reddit",
    "github",
    "gitlab",
    "pypi",
)


@pytest.mark.parametrize("platform", MAINSTREAM)
def test_every_platform_of_interest_is_registered(platform):
    capability = capability_for(platform)
    assert capability is not None, f"{platform} has no capability row"
    assert capability.domains
    assert capability.search_filters


def test_keybase_is_registered_even_though_it_has_no_url_shape_entry():
    """The registry must cover the gap between the two older tables."""
    assert capability_for("keybase") is not None
    assert capability_for("keybase").handle_check_supported


def test_the_registry_cannot_drift_from_the_url_classifier():
    known = {entry.key for entry in URL_PLATFORMS}
    assert set(_EXTRA) <= known, "an extras key names a platform the classifier does not know"


def test_a_platform_that_blocks_automation_is_never_marked_checkable():
    for capability in reference_only():
        assert (
            not capability.handle_check_supported
        ), f"{capability.platform} refuses anonymous requests; it must not be probed"
        assert capability.manual_search_supported, "a blocked platform is still worth searching"
        assert capability.notes or capability.fetch_note


def test_blocking_platforms_are_the_ones_we_expect():
    assert {item.platform for item in reference_only()} == {
        "linkedin",
        "instagram",
        "facebook",
        "snapchat",
        "twitter",
        "tiktok",
    }


def test_handle_checkable_platforms_all_declare_a_probe():
    for capability in handle_checkable():
        assert capability.probe_key, f"{capability.platform} claims a check with no endpoint"
        assert capability.server_fetchable


#: A handle each platform would actually accept. ORCID's is an iD, because an
#: iD is not a username and the classifier is right to refuse one that is not.
SAMPLE_HANDLES = {"orcid": "0000-0002-1825-0097"}


def test_a_profile_url_built_from_a_handle_classifies_back_to_its_platform():
    """The pattern and the classifier must agree, or a lead lands nowhere."""
    shapes = {entry.key for entry in URL_PLATFORMS}
    checked = 0
    for capability in CAPABILITIES:
        if capability.platform not in shapes:
            continue
        handle = SAMPLE_HANDLES.get(capability.platform, "example-person")
        url = capability.profile_url(handle)
        if url is None:
            continue
        classified = classify_url(url)
        assert classified is not None, url
        assert classified.platform == capability.platform, url
        assert classified.is_social, f"{url} is not profile-shaped"
        assert classified.handle
        checked += 1
    assert checked >= 10, "the round trip must actually cover the registry"


def test_an_orcid_url_built_from_a_username_is_not_a_profile():
    """A username is not an iD, and the classifier must not pretend otherwise."""
    classified = classify_url("https://orcid.org/example-person")
    assert classified is not None
    assert not classified.is_social


def test_a_handle_is_never_baked_into_a_pattern():
    for capability in CAPABILITIES:
        if capability.profile_url_pattern:
            assert "{handle}" in capability.profile_url_pattern


def test_search_platforms_are_bounded_and_ordered():
    ordered = search_platforms()
    assert 5 <= len(ordered) <= 12, "a worklist, not a directory"
    assert ordered[0].platform == "linkedin"


def test_the_summary_groups_every_platform():
    summary = summarise()
    covered = set(summary.directly_checked) | set(summary.manual_search_only)
    assert covered == {item.platform for item in CAPABILITIES}


def test_a_blocked_platform_says_plainly_that_nothing_was_retrieved():
    """A blocked platform's row explains the block, in the report's own words.

    Deliberately not a word blacklist over the prose: LinkedIn's note reads "no
    attempt is made to work around the block", which a keyword check flags as
    the very thing it promises. Whether the *code* works around a block is
    asserted against the code, in ``test_safety_boundaries``, which is where a
    guarantee like that belongs.
    """
    for capability in reference_only():
        text = f"{capability.notes} {capability.fetch_note}".lower()
        assert text.strip(), f"{capability.platform} must say why it is not fetched"
        assert (
            "not fetched" in text
            or "nothing is fetched" in text
            or "only the public url shape" in text
            or "manual search" in text
        ), f"{capability.platform} must say that nothing was retrieved"
