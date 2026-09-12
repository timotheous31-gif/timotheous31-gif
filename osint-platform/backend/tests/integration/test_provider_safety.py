"""Source-level guards on the new provider surface.

These tests read the source tree rather than exercising behaviour. That is
deliberate: the properties below are about code that must *not* exist, and the
only way to assert the absence of a capability is to look for it.

Every one of them corresponds to a line in the project's security constraints. A
constraint that lives only in a document drifts; a constraint with a test does
not.
"""

from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
REPO = BACKEND.parent

PROVIDER_DIR = APP / "services" / "providers"
NEW_MODULES = (
    PROVIDER_DIR / "search.py",
    PROVIDER_DIR / "anthropic_web_search.py",
    PROVIDER_DIR / "google_wss.py",
    APP / "services" / "enrichment.py",
    APP / "services" / "search_ingest.py",
    APP / "services" / "benchmark.py",
)


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------- one HTTP path, no other


def test_no_new_module_opens_a_second_http_path():
    """All outbound traffic goes through ``app.core.http`` and nothing else.

    That module is where the SSRF guard, the redirect validation, the byte
    ceiling, the timeout and the rate limiter live. A module that reaches for
    ``requests`` or builds its own ``httpx.AsyncClient`` bypasses all five at
    once, and it would do so silently.
    """
    forbidden_roots = {"requests", "urllib3", "aiohttp", "socket", "ftplib", "smtplib"}
    for path in NEW_MODULES:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                assert not names & forbidden_roots, f"{path.name} imports {names}"
                assert "httpx" not in names, f"{path.name} imports httpx directly"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots, f"{path.name} imports {root}"
                assert root != "httpx", f"{path.name} imports from httpx directly"


def test_enrichment_fetches_only_through_the_guarded_client():
    source = _source(APP / "services" / "enrichment.py")
    assert "from app.core import http" in source
    # Every fetch in the module is an ``http.`` call.
    assert "http.get(" in source
    assert "Client(" not in source


def test_the_anthropic_provider_never_disables_url_validation():
    """``validate=False`` exists for pre-validated literals in tests only."""
    source = _source(PROVIDER_DIR / "anthropic_web_search.py")
    assert "validate=False" not in source


# ----------------------------------------------- no scraping, no browser, no biometrics


def test_no_new_module_fetches_a_search_engine_result_page():
    """Scraping is forbidden; *blocking* a scraper's host is not.

    The test looks for an engine host behind a scheme — that is a fetch. The same
    hostnames appear bare in ``search_ingest.NOISE_HOSTS``, which exists to throw
    those results away, and a rule that could not tell the two apart would punish
    the defence along with the attack.
    """
    engine_hosts = (
        "google.com/search",
        "bing.com/search",
        "duckduckgo.com/html",
        "webcache.googleusercontent",
        "search.yahoo.com",
    )
    for path in NEW_MODULES:
        lowered = _source(path).lower()
        for host in engine_hosts:
            for scheme in ("http://", "https://"):
                assert f"{scheme}{host}" not in lowered, f"{path.name} fetches {host}"
                assert f"{scheme}www.{host}" not in lowered, f"{path.name} fetches {host}"


def test_no_new_module_drives_a_browser_or_defeats_a_control():
    """By import, not by substring: these are libraries, not words."""
    forbidden_roots = {
        "playwright",
        "selenium",
        "pyppeteer",
        "puppeteer",
        "undetected_chromedriver",
        "cloudscraper",
        "twocaptcha",
        "anticaptchaofficial",
    }
    for path in NEW_MODULES:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0].lower() for alias in node.names}
                assert not names & forbidden_roots, f"{path.name} imports {names}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0].lower()
                assert root not in forbidden_roots, f"{path.name} imports {root}"
    # And no proxy-rotation plumbing, which is the other way a control is defeated.
    for path in NEW_MODULES:
        lowered = _source(path).lower()
        for term in ("rotate_proxy", "proxy_pool", "proxy_rotation", "random.choice(proxies"):
            assert term not in lowered, f"{path.name} references {term}"


def test_no_new_module_attempts_authentication_or_a_private_surface():
    forbidden_terms = ("/login", "/signin", "/password", "/oauth/token", "cookie=", "set-cookie")
    for path in NEW_MODULES:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = node.value.lower()
                for term in forbidden_terms:
                    assert term not in lowered, f"{path.name} references {term}"


def test_no_new_module_touches_faces_or_biometrics():
    """The libraries and techniques, not the word.

    ``biometric_matching: False`` appears in the ingested-image record and is the
    platform *asserting* that it did none, so the check is for the capability
    being switched on rather than for the field existing.
    """
    forbidden_terms = (
        "face_recognition",
        "facenet",
        "insightface",
        "dlib",
        "reverse_image",
        "perceptual_hash",
        "phash(",
        "dhash(",
        "face_embedding",
    )
    for path in NEW_MODULES:
        lowered = _source(path).lower()
        for term in forbidden_terms:
            assert term not in lowered, f"{path.name} references {term}"
        assert '"biometric_matching": true' not in lowered
        assert "biometric_matching=true" not in lowered


def test_the_search_prompt_forbids_private_contact_detail_and_nationality_inference():
    """The brief the model is given is itself a policy surface."""
    from app.services.providers.anthropic_web_search import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    for required in (
        "home address",
        "personal phone",
        "personal email",
        "nationality",
        "citizenship",
        "residence",
    ):
        assert required in lowered, f"the system prompt does not address {required!r}"
    assert "do not summarise" in lowered


# ------------------------------------------------------ credentials and CI


def test_no_credential_is_hard_coded_anywhere_in_the_new_surface():
    """Including in the tests, which is where a real key most easily lands."""
    import re

    pattern = re.compile(r"sk-ant-(?!test|api03-SECRET|unrelated|other)[A-Za-z0-9_\-]{12,}")
    paths = [
        *NEW_MODULES,
        APP / "core" / "settings.py",
        BACKEND / "tests" / "unit" / "test_anthropic_web_search.py",
        BACKEND / "tests" / "integration" / "test_anthropic_discovery.py",
        BACKEND / "tests" / "integration" / "test_discovery_benchmark.py",
        REPO / "scripts" / "anthropic_web_search_smoke.py",
        REPO / ".env.example",
        REPO / "docker-compose.yml",
    ]
    for path in paths:
        assert not pattern.search(_source(path)), f"{path} looks like it contains a credential"


def test_the_env_example_ships_no_value_for_any_credential():
    for line in _source(REPO / ".env.example").splitlines():
        if line.startswith(("ANTHROPIC_API_KEY", "GOOGLE_WSS_API_KEY", "GOOGLE_WSS_CLIENT_ID")):
            assert line.split("=", 1)[1].strip() == "", line


def test_the_live_smoke_test_is_outside_the_collected_test_paths():
    """It makes a real billable call, so CI must not be able to collect it."""
    import tomllib

    config = tomllib.loads(_source(BACKEND / "pyproject.toml"))
    testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
    assert testpaths == ["tests"]
    script = REPO / "scripts" / "anthropic_web_search_smoke.py"
    assert script.exists()
    assert "tests" not in script.parts
    # And it refuses to do anything without a credential, so an accidental
    # invocation costs nothing.
    source = _source(script)
    assert 'os.environ.get("ANTHROPIC_API_KEY"' in source
    assert "return 1" in source


def test_no_test_in_the_suite_can_reach_the_real_anthropic_endpoint():
    """The hermetic guard, stated for this channel specifically.

    ``tests/conftest.py`` resolves ``api.anthropic.com`` to a documentation
    address and every test that uses it registers a respx route, so an unmocked
    request fails the suite rather than leaving the network.
    """
    conftest = _source(BACKEND / "tests" / "conftest.py")
    assert '"api.anthropic.com": "93.184.215.14"' in conftest
    assert "Test attempted to resolve" in conftest


def test_the_compliance_record_leaves_the_commercial_question_open():
    """A claimed right is worse than a recorded gap."""
    doc = _source(REPO / "docs" / "search-provider-compliance.md")
    assert "PENDING LEGAL/TERMS REVIEW" in doc
    assert "PENDING_PARTNER_ACCESS" in doc
    assert "citations must be included" in doc
    # The quotation is wrapped across lines in the document, so the assertion
    # reads the unwrapped text rather than depending on where it broke.
    unwrapped = " ".join(doc.replace("\n>", "\n").split())
    assert (
        "display citations as appropriate based on consultation with your legal team" in unwrapped
    )
    lowered = doc.lower()
    for overclaim in (
        "we may store",
        "we are permitted to redistribute",
        "redistribution is allowed",
    ):
        assert overclaim not in lowered
