"""Social URL classification for the platforms PR B adds, and the path rule.

Classification says what a URL *is*, never whose it is. The properties worth
pinning are the ones that stop a URL becoming an identity: a video page is not
a profile, a reserved path is not a handle, and a non-http scheme is refused
rather than coerced into something parseable.
"""

from __future__ import annotations

import pytest

from app.collectors.social import classify_url, platform_by_key


@pytest.mark.parametrize(
    ("url", "platform", "handle"),
    [
        ("https://www.linkedin.com/in/example-person", "linkedin", "example-person"),
        ("https://www.instagram.com/example_user", "instagram", "example_user"),
        ("https://www.facebook.com/example.person", "facebook", "example.person"),
        ("https://www.youtube.com/@examplechannel", "youtube", "examplechannel"),
        ("https://x.com/exampleuser", "twitter", "exampleuser"),
        ("https://twitter.com/exampleuser", "twitter", "exampleuser"),
        ("https://mobile.twitter.com/exampleuser", "twitter", "exampleuser"),
        ("https://www.tiktok.com/@exampleuser", "tiktok", "exampleuser"),
        ("https://www.snapchat.com/add/exampleuser", "snapchat", "exampleuser"),
        ("https://www.reddit.com/u/example", "reddit", "example"),
        ("https://github.com/octocat", "github", "octocat"),
    ],
)
def test_every_required_platform_is_classified(url, platform, handle):
    profile = classify_url(url)
    assert profile is not None
    assert profile.kind == "social"
    assert profile.platform == platform
    assert profile.handle == handle


@pytest.mark.parametrize(
    "url",
    [
        # A video is content, not a person — even though the path names one.
        "https://www.tiktok.com/@someone/video/7100000000000000000",
        "https://www.reddit.com/u/example/comments/abc123/a_post",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.linkedin.com/jobs/view/123456",
        "https://x.com/i/status/1000000000000000000",
        "https://x.com/home",
        "https://www.instagram.com/p/ABCDEFGHIJK",
    ],
)
def test_content_pages_are_not_profiles(url):
    """Recording a video page as somebody's profile invents an attribution."""
    profile = classify_url(url)
    assert profile is not None
    assert profile.kind == "web"
    assert profile.handle is None


@pytest.mark.parametrize(
    "platform", ["linkedin", "instagram", "facebook", "snapchat", "twitter", "tiktok"]
)
def test_login_gated_platforms_are_marked_unfetchable_with_a_reason(platform):
    """The block is recorded, never worked around."""
    definition = platform_by_key(platform)
    assert definition is not None
    assert definition.server_fetchable is False
    assert definition.fetch_note, "an unfetchable platform must say why"


@pytest.mark.parametrize("platform", ["github", "youtube", "reddit", "orcid"])
def test_openly_readable_platforms_are_not_marked_blocked(platform):
    definition = platform_by_key(platform)
    assert definition is not None
    assert definition.server_fetchable is True


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd", "ftp://example.org/x"],
)
def test_non_http_schemes_are_refused_rather_than_coerced(url):
    assert classify_url(url) is None


def test_x_and_twitter_are_the_same_platform():
    """Years of both are in circulation; an investigator will paste either."""
    assert (
        classify_url("https://x.com/a").platform == classify_url("https://twitter.com/a").platform
    )
