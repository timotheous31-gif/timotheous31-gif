"""The rules that keep discovery from becoming identification.

Every test here is a boundary. A handle is not a person; a block is not an
absence; a link somebody published is not proof of ownership; and a search
query is something a human runs, not something this codebase submits.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.collectors.capabilities import capability_for, reference_only
from app.collectors.person import PersonCandidate, PersonContext, anchor_matches
from app.collectors.social import classify_url
from app.services.recon import generate_queries

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "app"


# ------------------------------------------------------- URL classification

PROFILES = [
    ("https://www.linkedin.com/in/example-person", "linkedin", "example-person"),
    ("https://www.instagram.com/example.person", "instagram", "example.person"),
    ("https://www.facebook.com/example.person", "facebook", "example.person"),
    ("https://www.youtube.com/@example-person", "youtube", "example-person"),
    ("https://x.com/exampleperson", "twitter", "exampleperson"),
    ("https://twitter.com/exampleperson", "twitter", "exampleperson"),
    ("https://www.tiktok.com/@example.person", "tiktok", "example.person"),
    ("https://www.snapchat.com/add/example-person", "snapchat", "example-person"),
    ("https://www.reddit.com/user/example_person", "reddit", "example_person"),
    ("https://github.com/example-person", "github", "example-person"),
    ("https://gitlab.com/example-person", "gitlab", "example-person"),
    ("https://keybase.io/exampleperson", None, None),
    ("https://pypi.org/user/exampleperson", "pypi", "exampleperson"),
]


@pytest.mark.parametrize("url,platform,handle", PROFILES)
def test_public_profile_urls_are_classified(url, platform, handle):
    classified = classify_url(url)
    assert classified is not None
    if platform is None:
        # Not a platform this classifier knows by shape; it must stay a plain
        # web page rather than being guessed into somebody's profile.
        assert not classified.is_social
        return
    assert classified.platform == platform
    assert classified.handle == handle
    assert classified.is_social


NOT_PROFILES = [
    "https://www.linkedin.com/jobs/view/12345",
    "https://www.youtube.com/watch?v=abc123",
    "https://www.tiktok.com/@example.person/video/123",
    "https://www.reddit.com/r/example",
    "https://github.com/example-person/example-repo/issues/1",
]


@pytest.mark.parametrize("url", NOT_PROFILES)
def test_content_pages_never_become_somebodys_profile(url):
    classified = classify_url(url)
    assert classified is not None
    assert not classified.is_social, f"{url} was read as a person"


# --------------------------------------------- a handle is not an identity


def test_a_github_anchor_does_not_match_the_same_handle_elsewhere():
    """The investigator vouched for an account on GitHub, not for a string.

    This was a real defect: ``github_username`` sat in the general handle pool,
    so a LinkedIn account reusing the same handle scored as though the
    investigator had named it.
    """
    context = PersonContext(github_username="example-person")

    github = PersonCandidate(
        url="https://github.com/example-person",
        name="Example Person",
        identifiers={"github_login": "example-person"},
        handles=["example-person"],
    )
    assert [kind for kind, _ in anchor_matches(github, context)] == ["github_username"]

    linkedin = PersonCandidate(
        url="https://www.linkedin.com/in/example-person",
        name="Example Person",
        handles=["example-person"],
    )
    assert anchor_matches(linkedin, context) == []


def test_a_platform_agnostic_handle_still_matches_anywhere():
    """``known_usernames`` carries no platform, so it is not narrowed."""
    context = PersonContext(known_usernames=("example-person",))
    linkedin = PersonCandidate(
        url="https://www.linkedin.com/in/example-person",
        name="Example Person",
        handles=["example-person"],
    )
    assert [kind for kind, _ in anchor_matches(linkedin, context)] == ["username"]


# -------------------------------------------------- a block is not an absence


def test_a_blocked_platform_is_never_reported_as_missing():
    for capability in reference_only():
        assert capability.manual_search_supported
        assert not capability.handle_check_supported
        text = f"{capability.notes} {capability.fetch_note}".lower()
        for phrase in ("does not exist", "no profile", "not found", "no account"):
            assert (
                phrase not in text
            ), f"{capability.platform}: a refusal to answer is not an answer"


def test_a_reference_lead_withholds_its_handle_from_the_anchor_engine():
    from app.collectors.person_usernames import PersonUsernameCollector

    collector = PersonUsernameCollector()
    leads, note = collector._reference_leads(["example-person"])
    assert leads, "a supplied handle must produce leads on the blocking platforms"
    assert "not evidence" in note
    for lead in leads:
        assert lead.handles == [], "a lead's handle must not score against itself"
        assert lead.extra["signal"] == "SAME_USERNAME"
        assert lead.extra["verification"] == "manual_required"
        assert lead.extra["access"] == "reference_only"
        # And the anchor engine agrees, given the very handle it was built from.
        context = PersonContext(known_usernames=("example-person",))
        assert anchor_matches(lead, context) == []


def test_reference_leads_are_bounded():
    from app.collectors.person_usernames import MAX_REFERENCE_LEADS, PersonUsernameCollector

    leads, _note = PersonUsernameCollector()._reference_leads(["a", "b", "c", "d", "e"])
    assert len(leads) <= MAX_REFERENCE_LEADS


# ---------------------------------------------------- manual search queries


def _queries(context: PersonContext | None = None, **kwargs):
    return [query.query for query in generate_queries("Example Person", context, **kwargs)]


@pytest.mark.parametrize(
    "site",
    [
        "linkedin.com",
        "instagram.com",
        "facebook.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "youtube.com",
        "reddit.com",
        "github.com",
        "snapchat.com",
    ],
)
def test_every_mainstream_platform_gets_a_site_query(site):
    assert f'"Example Person" site:{site}' in _queries()


def test_profile_shaped_filters_are_generated_where_they_exist():
    queries = _queries()
    assert '"Example Person" site:linkedin.com/in' in queries
    assert '"Example Person" site:youtube.com/@' in queries
    assert '"Example Person" site:reddit.com/user' in queries


def test_image_recon_queries_are_generated():
    queries = _queries()
    assert '"Example Person" photo' in queries
    assert '"Example Person" image' in queries


def test_handle_queries_target_the_platforms_we_cannot_check():
    queries = _queries(PersonContext(github_username="example-person"))
    assert '"example-person"' in queries
    assert '"example-person" LinkedIn' in queries
    assert '"example-person" Instagram' in queries
    # GitHub is checked directly, so it needs no manual handle query.
    assert '"example-person" GitHub' not in queries


def test_a_declared_name_becomes_an_extra_search_not_a_replacement():
    queries = _queries(also_known_as=("Example Person Dass",))
    assert '"Example Person Dass"' in queries
    assert '"Example Person"' in queries, "the searched name is never dropped"


def test_the_query_list_stays_a_worklist():
    from app.services.recon import MAX_QUERIES

    context = PersonContext(
        known_usernames=("one", "two"),
        github_username="three",
        organizations=("Example Institute", "Example Trust"),
        country="Example Republic",
        occupation="Applied Linguist",
    )
    assert len(_queries(context)) <= MAX_QUERIES


def test_nothing_in_this_codebase_retrieves_a_search_result_page():
    """Queries are generated for a human. No engine is ever requested."""
    # The consumer search UIs, matched exactly. A vendor API on a domain that
    # merely contains "google" — google.serper.dev is one — is a documented,
    # optional paid API and not a result page, so it must not trip this.
    engine_hosts = re.compile(
        r"https?://(?:www\.)?(?:google\.com|bing\.com|duckduckgo\.com|startpage\.com)/",
        re.I,
    )
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if engine_hosts.search(line) and not line.lstrip().startswith(("#", '"', "'", "*")):
                offenders.append(f"{path.name}:{number}: {line.strip()[:70]}")
    assert not offenders, offenders


def test_capability_lookup_is_forgiving_but_closed():
    assert capability_for("GitHub") is not None
    assert capability_for(" github ") is not None
    assert capability_for("myspace") is None
    assert capability_for("") is None
