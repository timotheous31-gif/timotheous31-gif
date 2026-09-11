"""The boundaries this platform will not cross, asserted rather than asserted-to.

Every item here corresponds to a line in the DO NOT IMPLEMENT list. They are
tests rather than comments because a documented limit that nothing checks is a
limit that quietly erodes as the codebase grows.
"""

from __future__ import annotations

import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Libraries that would perform biometric identification. None may be imported.
BIOMETRIC_PACKAGES = (
    "face_recognition",
    "deepface",
    "insightface",
    "facenet",
    "dlib",
    "mediapipe",
    "cv2",
    "opencv",
)

#: Techniques that would defeat access controls rather than respect them.
EVASION_MARKERS = (
    "captcha",
    "anticaptcha",
    "2captcha",
    "solve_captcha",
    "rotate_proxy",
    "proxy_rotation",
    "residential_proxy",
    "undetected_chromedriver",
    "selenium_stealth",
    "puppeteer_stealth",
)


def _sources() -> list[pathlib.Path]:
    return [path for path in BACKEND.rglob("*.py") if "__pycache__" not in str(path)]


@pytest.mark.parametrize("package", BIOMETRIC_PACKAGES)
def test_no_biometric_library_is_imported(package):
    pattern = re.compile(rf"^\s*(?:import|from)\s+{re.escape(package)}\b", re.MULTILINE)
    offenders = [str(path) for path in _sources() if pattern.search(path.read_text("utf-8"))]
    assert not offenders, f"{package} imported in {offenders}"


@pytest.mark.parametrize("marker", EVASION_MARKERS)
def test_no_anti_bot_evasion_machinery_exists(marker):
    offenders = []
    for path in _sources():
        text = path.read_text("utf-8").lower()
        # Skip the line that names the marker in this very list, and prose that
        # documents the refusal — those are the opposite of an implementation.
        for line in text.splitlines():
            if marker in line and not line.lstrip().startswith(("#", '"', "*", "'")):
                offenders.append(f"{path}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_no_reverse_image_search_provider_is_configured():
    """Reverse image search is how an unknown person gets identified from a photo."""
    pattern = re.compile(r"(reverse.?image|tineye|yandex.?image|search.?by.?image)", re.I)
    offenders = []
    for path in _sources():
        for line in path.read_text("utf-8").splitlines():
            if pattern.search(line) and not line.lstrip().startswith(("#", '"', "*", "'")):
                offenders.append(f"{path}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_the_image_schema_has_no_field_that_could_hold_a_biometric_result():
    from app.models import ImageEvidence

    columns = {column.name.lower() for column in ImageEvidence.__table__.columns}
    for forbidden in ("embedding", "descriptor", "face", "similarity", "distance", "match_score"):
        assert not any(forbidden in name for name in columns), forbidden


def test_google_html_is_never_fetched():
    """The platform generates queries; a human runs them in their own browser.

    Matched against the consumer *search UI* hosts specifically. A vendor API on
    a domain that happens to contain "google" — google.serper.dev is one — is a
    documented, optional paid API, not HTML scraping, and must not trip this.
    """
    pattern = re.compile(
        r"https?://(www\.)?(google|bing|duckduckgo)\.[a-z]{2,3}(\.[a-z]{2})?/(search|html)", re.I
    )
    offenders = []
    for path in _sources():
        for line in path.read_text("utf-8").splitlines():
            if pattern.search(line) and not line.lstrip().startswith(("#", '"', "*", "'")):
                offenders.append(f"{path}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_no_credential_or_login_flow_exists_for_a_third_party_platform():
    """No code that authenticates *to* a platform in order to read gated content.

    Assignments rather than names: `_login` in the GitHub collector derives a
    public username from a target and is exactly the sort of harmless identifier
    that makes a name-based check cry wolf until somebody deletes it.
    """
    pattern = re.compile(
        r"(password\s*=\s*[\"'f]|session_cookie\s*=|auth_token\s*=|"
        r"login\(.*password|\bsign_?in\()",
        re.I,
    )
    offenders = []
    for path in _sources():
        for line in path.read_text("utf-8").splitlines():
            if pattern.search(line) and not line.lstrip().startswith(("#", '"', "*", "'")):
                offenders.append(f"{path}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_login_gated_platforms_declare_the_block_instead_of_a_workaround():
    from app.collectors.social import PLATFORMS

    blocked = [item for item in PLATFORMS if not item.server_fetchable]
    assert blocked, "the platforms known to refuse anonymous access must be marked"
    for platform in blocked:
        assert platform.fetch_note, f"{platform.key} must say why it is not fetched"
        note = platform.fetch_note.lower()
        assert "only the public url shape" in note or "not" in note


def test_the_github_profile_page_is_read_through_the_api_never_scraped():
    """GitHub publishes the profile README as data. That is what we ask for.

    The rendered page at ``github.com/<login>`` carries the same content, and
    parsing it would be scraping a page the platform already offers as an API —
    against the rule, and worse evidence besides.
    """
    from app.collectors import github_profile

    source = pathlib.Path(github_profile.__file__).read_text("utf-8")
    fetched = re.findall(r"http\.get\(\s*\n?\s*f?\"([^\"]+)\"", source)
    assert fetched, "the module must fetch something, or this test proves nothing"
    for url in fetched:
        assert url.startswith("{api}/"), f"{url} is not a documented API endpoint"
    assert "https://github.com/" not in source.replace('f"https://github.com/', "URL_BUILD")


def test_no_extracted_profile_fact_can_be_a_nationality_or_a_citizenship():
    """A country on a profile is where somebody places themselves in public.

    Nationality and citizenship are legal statuses. Naming a place asserts
    neither, so the vocabulary has no kind that could carry one — which makes
    the limit structural rather than a rule somebody has to remember.
    """
    from app.collectors.github_profile import GEOGRAPHIC_INTERPRETATION, KIND_LABELS

    for kind, label in KIND_LABELS.items():
        for forbidden in ("national", "citizen", "passport", "residen", "ethnic", "religio"):
            assert forbidden not in kind.lower(), f"{kind} names a protected status"
            assert forbidden not in label.lower(), f"{label} names a protected status"
    assert "not a claim of nationality" in GEOGRAPHIC_INTERPRETATION.lower()


def test_no_email_address_is_ever_constructed_from_a_name_and_a_domain():
    """The one shape that would produce a plausible, unfounded finding.

    Matched on construction, not on the word "email": an f-string or a
    concatenation that puts an ``@`` between two variables is the pattern, and
    nothing in the codebase may contain one.
    """
    pattern = re.compile(r"[\"']\{[a-z_]+\}@\{[a-z_]+\}|[\"']@[\"']\s*\+|\+\s*[\"']@[\"']", re.I)
    offenders = []
    for path in _sources():
        for line in path.read_text("utf-8").splitlines():
            if pattern.search(line) and not line.lstrip().startswith(("#", '"', "*", "'")):
                offenders.append(f"{path}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_no_provider_adapter_targets_a_consumer_search_result_page():
    """Search *APIs* are documented products; result pages are not to be parsed.

    A vendor API on a domain containing "google" — google.serper.dev is one — is
    a documented paid API and is allowed. What is forbidden is a request to the
    consumer search UI, which is what scraping would look like.
    """
    pattern = re.compile(
        r"https?://(?:www\.)?(?:google\.com|bing\.com|duckduckgo\.com|startpage\.com)/",
        re.I,
    )
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith(("#", '"', "'", "*")):
                offenders.append(f"{path.name}:{number}: {line.strip()[:70]}")
    assert not offenders, offenders


def test_no_html_parser_is_applied_to_a_search_response():
    """Search results are read from JSON APIs; no markup is ever parsed.

    Scoped to the search path deliberately. ``http_meta`` does parse HTML — the
    title and OpenGraph tags of a page the investigation is actually about — and
    that is a different act from parsing a search engine's result page. What must
    stay true is that nothing in the search path can do it.
    """
    banned = ("beautifulsoup", "bs4", "lxml.html", "html.parser", "pyquery", "selectolax")
    search_path = (
        BACKEND / "services" / "providers" / "search.py",
        BACKEND / "services" / "search_ingest.py",
        BACKEND / "services" / "recon.py",
        BACKEND / "services" / "recon_import.py",
        BACKEND / "collectors" / "search.py",
    )
    offenders = []
    for path in search_path:
        lowered = path.read_text("utf-8").lower()
        for term in banned:
            if f"import {term}" in lowered or f"from {term}" in lowered:
                offenders.append(f"{path.name}: {term}")
    assert not offenders, offenders


def test_a_search_result_url_is_validated_before_it_is_stored():
    """Provider output is third-party input, held to the pasted-URL standard."""
    from app.services.search_ingest import public_url

    for unsafe in (
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://10.1.2.3/",
        "http://192.168.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pass@example.com/",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "",
    ):
        assert public_url(unsafe) is None, unsafe
    assert public_url("https://example.org/team/person") == "https://example.org/team/person"


def test_nothing_infers_a_nationality_from_a_place():
    """Wikidata's citizenship claim is kept apart from anything comparable.

    It was being read into ``candidate.locations``, where the anchor engine
    compares a supplied city or country — which made a citizenship claim
    matchable against a place. That is the nationality inference this platform
    refuses, in the one place it would have been invisible.
    """
    from app.collectors.wikidata import (
        CITIZENSHIP_INTERPRETATION,
        CITIZENSHIP_PROPERTY,
        LOCATION_PROPERTIES,
    )

    assert CITIZENSHIP_PROPERTY == "P27"
    assert CITIZENSHIP_PROPERTY not in LOCATION_PROPERTIES
    assert LOCATION_PROPERTIES == ()
    lowered = CITIZENSHIP_INTERPRETATION.lower()
    assert "not a location" in lowered
    assert "infers a nationality" in lowered


def test_a_citizenship_claim_never_reaches_the_anchor_comparison():
    from app.collectors.person import PersonCandidate, PersonContext, anchor_matches

    # A candidate carrying only a citizenship claim, and a supplied country.
    candidate = PersonCandidate(
        url="https://www.wikidata.org/wiki/Q1",
        name="Example Person",
        extra={"citizenship_claims": ["Pakistan"]},
    )
    assert anchor_matches(candidate, PersonContext(country="Pakistan")) == []


# ---------------------------------------------------- what a match may rest on


def test_two_institutions_sharing_only_generic_words_do_not_corroborate():
    """The strongest non-identifier signal may not fire on "university".

    ``context_affiliation_match`` scores 0.50 with a floor of 0.50 — it takes a
    name-only candidate from 0.15 to 0.575 on its own. It used to fire on any one
    shared word over two letters, so "University of Karachi" corroborated
    "University of Sindh" and "Ministry of Education" corroborated "Ministry of
    Health". In a sector where nearly every employer's name is assembled from
    those words, that is a machine for promoting strangers.
    """
    from app.collectors.person import PersonCandidate, PersonContext, anchor_matches

    def matched(supplied: str, observed: str) -> bool:
        candidate = PersonCandidate(
            url="https://example.org/person", name="Example Person", affiliations=[observed]
        )
        kinds = [
            kind for kind, _ in anchor_matches(candidate, PersonContext(organizations=(supplied,)))
        ]
        return "affiliation" in kinds

    for supplied, observed in (
        ("University of Karachi", "University of Sindh"),
        ("Government College University", "Government of Sindh"),
        ("Ministry of Education", "Ministry of Health"),
        ("National Institute of Technology", "National Institute of Health"),
        ("Higher Education Commission", "Education Department"),
    ):
        assert not matched(supplied, observed), f"{supplied!r} must not corroborate {observed!r}"

    # And the matches that must survive: a shared *distinctive* word, or the
    # same name written with more or less of its address.
    for supplied, observed in (
        ("University of Sindh", "University of Sindh"),
        ("University of Sindh", "University of Sindh, Jamshoro"),
        ("Shah Abdul Latif University", "Shah Abdul Latif University Khairpur"),
        ("Aga Khan University", "The Aga Khan University Hospital"),
    ):
        assert matched(supplied, observed), f"{supplied!r} must corroborate {observed!r}"


def test_a_profession_must_match_as_a_phrase_not_one_shared_word():
    from app.collectors.person import PersonCandidate, PersonContext, anchor_matches

    def matched(occupation: str, summary: str) -> bool:
        candidate = PersonCandidate(
            url="https://example.org/person", name="Example Person", summary=summary
        )
        kinds = [
            kind for kind, _ in anchor_matches(candidate, PersonContext(occupation=occupation))
        ]
        return "occupation" in kinds

    assert not matched("assistant professor", "Assistant Manager at Example Bank")
    assert matched("lecturer", "Lecturer in English")
    assert matched("applied linguist", "Applied linguist and lecturer")


def test_no_name_variant_claims_a_role_for_a_name_part():
    """A three-part name is not reliably "first, middle, last".

    "Tabitha Afzal Imdad" may carry a patronymic where a Western reading expects
    a middle name, and nothing in this system learns which. So a variant kind,
    its label, its explanation and its confidence rule all describe what happened
    to a *token*, never what role that token plays in the person's name.
    """
    from app.correlation.confidence import default_engine
    from app.services.name_variants import (
        VARIANT_LABELS,
        VARIANT_ORDER,
        generate_variants,
    )

    forbidden = (
        "middle name",
        "first name",
        "last name",
        "surname",
        "given name",
        "family name",
        "maiden name",
    )
    texts = [
        *VARIANT_ORDER,
        *VARIANT_LABELS.values(),
        *[variant.reason for variant in generate_variants("Tabitha Afzal Imdad Khan")],
        *[
            rule.reason
            for key, rule in default_engine.rules.items()
            if key.startswith(("name_variant", "same_person_name"))
        ],
    ]
    for text in texts:
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{phrase!r} appears in {text!r}"
