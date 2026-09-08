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
