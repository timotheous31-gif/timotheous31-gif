"""GitHub profile enrichment: the special profile repository and its README.

GitHub gives every account a documented way to publish a personal page: a public
repository whose name equals the login, whose README is rendered on the profile.
It is the one place on GitHub where a person writes about themselves in prose,
and the platform was ignoring it entirely — which is how an investigation could
report a GitHub account while missing that its owner states their post, their
employer and their field on the profile itself.

Two boundaries shape this module.

**Bounded, not a crawler.** Exactly two documented API calls per login:
``GET /repos/{login}/{login}`` and ``GET /repos/{login}/{login}/readme``. No
repository listing, no following of links found in the README, no second page.
The purpose is profile enrichment; a general link-follower is a crawler and
would need its own design.

**Explicit, not inferred.** Every fact here is something the README states.
Extraction recognises two shapes — a labelled field the author wrote
(``Occupation: ...``) and a phrase built from a closed vocabulary — and each
resulting claim carries the verbatim source line it came from, so a reader
audits the transformation rather than trusting it. A country named on a profile
is recorded as a *public geographic association*, never as nationality or
citizenship: those are legal statuses, and no README asserts one by naming a
place.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core import http
from app.core.logging import get_logger
from app.core.ratelimit import RetryPolicy

log = get_logger(__name__)

#: Decoded README bytes we are willing to read. A profile README is prose; a
#: megabyte of it is a generated artefact, not a self-description.
README_MAX_BYTES = 128 * 1024

#: Lines scanned for unlabelled phrases. A profile README states who the person
#: is at the top and then talks about projects, so phrase recognition is
#: confined to that header region. Labelled fields are honoured anywhere,
#: because a label is the author saying what the value means.
HEADER_LINES = 30

#: Caps on what one README may contribute. A README is a page, not a feed.
MAX_FACTS = 24
MAX_LINKS = 20
MAX_IMAGES = 10
MAX_EMAILS = 5

#: Generated status images — build badges, view counters, rendered stat cards.
#: They are pictures of a number, not published photographs, and promoting
#: twenty of them as image evidence would bury the one that matters.
BADGE_HOSTS = frozenset(
    {
        "img.shields.io",
        "shields.io",
        "badgen.net",
        "badge.fury.io",
        "forthebadge.com",
        "komarev.com",
        "visitor-badge.laobi.icu",
        "visitor-badge.glitch.me",
        "github-readme-stats.vercel.app",
        "github-readme-streak-stats.herokuapp.com",
        "github-profile-trophy.vercel.app",
        "readme-typing-svg.herokuapp.com",
        "streak-stats.demolab.com",
        "codecov.io",
        "app.codecov.io",
        "travis-ci.org",
        "travis-ci.com",
        "api.codeclimate.com",
    }
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", re.I)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)")
HTML_IMG_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
HTML_HREF_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
BARE_URL_RE = re.compile(r"(?<![\w(<\"'])(https?://[^\s<>()\[\]\"']+)")

#: Separators an author uses between the parts of a one-line self-description.
#: A hyphen only counts when it is spaced, so ``(BPS-17)`` stays intact.
SEGMENT_RE = re.compile("\\s+[-\u2013\u2014]\\s+|\\s*[,;|\u2022\u00b7]\\s*|\\s{3,}")

#: Labels an author may write, mapped to the claim they introduce. A closed
#: vocabulary: an unrecognised label produces no claim rather than a guess.
LABELS: dict[str, str] = {
    "name": "declared_name",
    "full name": "declared_name",
    "declared name": "declared_name",
    "occupation": "occupation",
    "title": "occupation",
    "job title": "occupation",
    "job": "occupation",
    "role": "occupation",
    "position": "occupation",
    "designation": "occupation",
    "post": "occupation",
    "profession": "occupation",
    "currently": "occupation",
    "employer": "employer",
    "organisation": "employer",
    "organization": "employer",
    "company": "employer",
    "institution": "employer",
    "affiliation": "employer",
    "department": "employer",
    "workplace": "employer",
    "works at": "employer",
    "working at": "employer",
    "university": "employer",
    "field": "professional_field",
    "fields": "professional_field",
    "discipline": "professional_field",
    "specialisation": "professional_field",
    "specialization": "professional_field",
    "speciality": "professional_field",
    "specialty": "professional_field",
    "expertise": "professional_field",
    "research interests": "professional_field",
    "research interest": "professional_field",
    "interests": "professional_field",
    "focus": "professional_field",
    "location": "location",
    "based in": "location",
    "based": "location",
    "country": "location",
    "city": "location",
    "region": "location",
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    "contact": "email",
    "website": "website",
    "web": "website",
    "homepage": "website",
    "site": "website",
    "blog": "website",
    "portfolio": "website",
    "orcid": "identifier",
    "scopus": "identifier",
    "researcherid": "identifier",
    "publons": "identifier",
    "google scholar": "identifier",
    "scholar": "identifier",
}
LABEL_RE = re.compile(
    "^(?P<label>[A-Za-z][A-Za-z \\-/]{1,28}?)\\s*[:\uff1a]\\s*(?P<value>\\S.*)$",
)

#: Occupational nouns. Recognising one makes a segment a role claim; the word
#: itself never becomes the value, because the value is what the author wrote.
_ROLE_WORDS = """
    lecturer professor teacher tutor instructor educator principal headmaster
    researcher scientist scholar fellow academic linguist economist historian
    mathematician statistician psychologist sociologist biologist chemist
    physicist geologist engineer developer programmer architect designer
    analyst consultant advisor adviser strategist administrator manager
    director officer executive coordinator supervisor specialist technician
    physician doctor surgeon dentist nurse pharmacist therapist veterinarian
    lawyer advocate attorney solicitor barrister judge magistrate notary
    accountant auditor actuary banker economist journalist reporter editor
    author writer translator interpreter photographer illustrator animator
    musician composer artist curator librarian archivist
    student candidate intern apprentice trainee graduate undergraduate
    entrepreneur founder cofounder freelancer contractor
    """
ROLE_TERMS = frozenset(_ROLE_WORDS.split())

#: Markers that turn a role phrase into a *post*: a subject, a place of work, a
#: grade or a seniority level. A role phrase without one is a description of
#: what the person does — their field — rather than the job they hold.
POST_MARKER_RE = re.compile(
    r"\b(?:in|of|at|for)\b|\(|"
    r"\b(?:senior|junior|assistant|associate|chief|head|principal|lead|deputy"
    r"|vice|adjunct|visiting|emeritus|trainee|apprentice)\b",
    re.I,
)

#: Words that make a segment an organisation. Recognised anywhere in the
#: segment; the whole segment is the value.
_ORG_WORDS = """
    government ministry directorate department secretariat authority bureau
    commission council board agency administration
    university college school academy institute institution polytechnic
    seminary madrasa gymnasium conservatoire conservatory
    hospital clinic infirmary dispensary laboratory laboratories
    company corporation corp inc incorporated ltd limited llc llp plc gmbh
    ag sa nv bv pty holdings group enterprises industries
    foundation trust charity society association federation union guild
    consortium partnership cooperative
    bank insurance
    """
ORG_TERMS = frozenset(_ORG_WORDS.split())

#: Country names, for recognising a *public geographic association*. Never a
#: nationality: naming a country on a profile says where a person situates
#: themselves publicly, not what passport they hold.
_COUNTRY_NAMES = """
    Afghanistan|Albania|Algeria|Andorra|Angola|Antigua and Barbuda|Argentina|Armenia|
    Australia|Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|
    Belize|Benin|Bhutan|Bolivia|Bosnia and Herzegovina|Botswana|Brazil|Brunei|Bulgaria|
    Burkina Faso|Burundi|Cambodia|Cameroon|Canada|Cape Verde|Central African Republic|
    Chad|Chile|China|Colombia|Comoros|Congo|Costa Rica|Croatia|Cuba|Cyprus|Czechia|
    Czech Republic|Denmark|Djibouti|Dominica|Dominican Republic|Ecuador|Egypt|
    El Salvador|Equatorial Guinea|Eritrea|Estonia|Eswatini|Ethiopia|Fiji|Finland|France|
    Gabon|Gambia|Georgia|Germany|Ghana|Greece|Grenada|Guatemala|Guinea|Guinea-Bissau|
    Guyana|Haiti|Honduras|Hong Kong|Hungary|Iceland|India|Indonesia|Iran|Iraq|Ireland|
    Israel|Italy|Ivory Coast|Jamaica|Japan|Jordan|Kazakhstan|Kenya|Kiribati|Kosovo|
    Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|Liberia|Libya|Liechtenstein|Lithuania|
    Luxembourg|Macau|Madagascar|Malawi|Malaysia|Maldives|Mali|Malta|Marshall Islands|
    Mauritania|Mauritius|Mexico|Micronesia|Moldova|Monaco|Mongolia|Montenegro|Morocco|
    Mozambique|Myanmar|Namibia|Nauru|Nepal|Netherlands|New Zealand|Nicaragua|Niger|
    Nigeria|North Korea|North Macedonia|Norway|Oman|Pakistan|Palau|Palestine|Panama|
    Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Qatar|Romania|Russia|
    Rwanda|Saint Kitts and Nevis|Saint Lucia|Samoa|San Marino|Saudi Arabia|Senegal|
    Serbia|Seychelles|Sierra Leone|Singapore|Slovakia|Slovenia|Solomon Islands|Somalia|
    South Africa|South Korea|South Sudan|Spain|Sri Lanka|Sudan|Suriname|Sweden|
    Switzerland|Syria|Taiwan|Tajikistan|Tanzania|Thailand|Timor-Leste|Togo|Tonga|
    Trinidad and Tobago|Tunisia|Turkey|Turkmenistan|Tuvalu|Uganda|Ukraine|
    United Arab Emirates|United Kingdom|United States|United States of America|Uruguay|
    Uzbekistan|Vanuatu|Vatican City|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe|
    UK|USA|UAE
    """
COUNTRIES: frozenset[str] = frozenset(
    name.strip().lower() for name in _COUNTRY_NAMES.split("|") if name.strip()
)

#: What each claim kind means, carried into the report so the limit travels with
#: the data. The geographic one is the reason this mapping exists at all.
KIND_LABELS: dict[str, str] = {
    "declared_name": "Declared name",
    "occupation": "Occupation / title",
    "employer": "Organisation / employer association",
    "professional_field": "Professional field",
    "location": "Public geographic association",
    "email": "Public email",
    "website": "Public website",
    "identifier": "Public identifier",
}

#: Stated on every geographic claim. A country on a profile is where somebody
#: places themselves in public, and that is all it is.
GEOGRAPHIC_INTERPRETATION = (
    "Public geographic association only: the profile names this place. It is not "
    "a claim of nationality, citizenship, residence or current location."
)


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """One explicitly stated fact, and the line that states it."""

    kind: str
    value: str
    source_line: str
    #: ``labelled`` — the author wrote ``Occupation: ...``. ``phrase`` — the
    #: segment matched the closed vocabulary named in ``matched_term``.
    basis: str
    matched_term: str = ""

    def to_dict(self, *, source_url: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "label": KIND_LABELS.get(self.kind, self.kind),
            "value": self.value,
            "source_line": self.source_line,
            "basis": self.basis,
            "matched_term": self.matched_term or None,
            "source_url": source_url,
        }
        if self.kind == "location":
            payload["interpretation"] = GEOGRAPHIC_INTERPRETATION
        return payload


@dataclass(slots=True)
class ProfileRepository:
    """The special ``login/login`` repository, if GitHub has one to show."""

    exists: bool
    note: str
    full_name: str = ""
    html_url: str = ""
    description: str | None = None
    homepage: str | None = None
    default_branch: str = "main"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "note": self.note,
            "full_name": self.full_name or None,
            "html_url": self.html_url or None,
            "description": self.description,
            "homepage": self.homepage,
            "default_branch": self.default_branch,
        }


@dataclass(slots=True)
class ProfileEnrichment:
    """Everything the profile repository published, ready to be promoted."""

    login: str
    repository: ProfileRepository
    readme_url: str | None = None
    readme_sha: str | None = None
    readme_note: str = ""
    facts: list[ProfileFact] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    @property
    def retrieved(self) -> bool:
        return self.readme_url is not None

    def values(self, kind: str) -> list[str]:
        return [fact.value for fact in self.facts if fact.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_repository": self.repository.to_dict(),
            "readme_url": self.readme_url,
            "readme_sha": self.readme_sha,
            "readme_note": self.readme_note or None,
            "readme_facts": [fact.to_dict(source_url=self.readme_url) for fact in self.facts],
            "readme_emails": list(self.emails),
            "readme_links": list(self.links),
            "readme_images": list(self.images),
        }


# ------------------------------------------------------------------ markdown


def _strip_markdown(line: str) -> str:
    """Reduce one Markdown line to the text a reader sees.

    Images vanish, links keep their text, emphasis and headings lose their
    punctuation. Nothing is rewritten: what remains is what the author typed.
    """
    text = MD_IMAGE_RE.sub(" ", line)
    text = MD_LINK_RE.sub(lambda match: match.group(1), text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"^\s*>+\s*", "", text)
    text = re.sub(r"^\s*(?:[-+*]|\d{1,2}[.)])\s+", "", text)
    text = re.sub(r"^\s*\|", "", text)
    # A table row's trailing pipe and the |---| separator carry no content.
    text = re.sub(r"\|\s*$", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[\s|:\-=_*+#]*", line))


def _segments(line: str) -> list[str]:
    """Split a self-description into the parts an author separated."""
    parts = [part.strip(" .\u00b7\u2014\u2013-") for part in SEGMENT_RE.split(line)]
    return [part for part in parts if part]


#: Openers an author writes before a self-description. Stripped so the claim
#: is the description itself, not the sentence that introduced it.
OPENER_RE = re.compile(
    r"^(?:hi|hello|hey)?[\s,!]*"
    "(?:i\\s*['\u2019]?\\s*a?m|i\\s+am|my\\s+name\\s+is|currently|presently|"
    r"working\s+as|serving\s+as|employed\s+as)\s+",
    re.I,
)
ARTICLE_RE = re.compile(r"^(?:an?|the)\s+", re.I)
#: Prepositions that join a post to the organisation that grants it.
JOINER_RE = re.compile(r"\s+(?:at|with|for)\s+", re.I)


def _plausible_segment(segment: str) -> bool:
    """A claim is a phrase, not a paragraph."""
    return 1 < len(segment) <= 120 and len(segment.split()) <= 12


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


# ---------------------------------------------------------------- extraction


def extract_facts(readme: str) -> list[ProfileFact]:
    """Facts the README explicitly states.

    Labelled fields are read wherever they appear, because a label is the
    author declaring what a value means. Unlabelled phrases are read only in the
    header region, and only when a closed vocabulary recognises them — so a
    project description mentioning an engineer does not become an occupation.
    """
    facts: list[ProfileFact] = []
    seen: set[tuple[str, str]] = set()

    def add(fact: ProfileFact) -> None:
        key = (fact.kind, fact.value.lower())
        if key in seen or len(facts) >= MAX_FACTS:
            return
        seen.add(key)
        facts.append(fact)

    raw_lines = readme.splitlines()
    header_budget = HEADER_LINES
    in_code = False
    for raw in raw_lines:
        if raw.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        line = _strip_markdown(raw)
        if not line or _is_separator(line):
            continue

        labelled = _labelled_fact(line)
        if labelled is not None:
            add(labelled)
            continue

        if header_budget <= 0:
            continue
        header_budget -= 1
        for segment in _segments(line):
            for phrase in _phrase_facts(segment, line):
                add(phrase)
    return facts


def _labelled_fact(line: str) -> ProfileFact | None:
    """``Occupation: Lecturer in English`` — the author named the field."""
    match = LABEL_RE.match(line)
    if match is None:
        return None
    label = re.sub(r"\s+", " ", match.group("label")).strip().lower()
    kind = LABELS.get(label)
    if kind is None:
        return None
    value = match.group("value").strip().strip("|").strip()
    if kind == "email":
        found = EMAIL_RE.search(value)
        if found is None:
            return None
        value = found.group(0)
    if not value or len(value) > 200:
        return None
    if kind == "location" and value.lower() not in COUNTRIES and len(value.split()) > 6:
        return None
    return ProfileFact(
        kind=kind, value=value, source_line=line, basis="labelled", matched_term=label
    )


def _phrase_fact(segment: str, line: str) -> ProfileFact | None:
    """Recognise one segment of an unlabelled self-description.

    Ordered most specific first. A country is matched on the whole segment, an
    organisation and a role on a vocabulary word inside it — and the value is
    always the segment as written, never the word that triggered the match.
    """
    for _ in range(3):
        stripped = ARTICLE_RE.sub("", OPENER_RE.sub("", segment)).strip()
        if stripped == segment:
            break
        segment = stripped
    if not _plausible_segment(segment):
        return None

    lowered = segment.lower().strip(".")
    if lowered in COUNTRIES:
        return ProfileFact(
            kind="location", value=segment, source_line=line, basis="phrase", matched_term=lowered
        )

    words = _word_set(segment)
    organisation = words & ORG_TERMS
    if organisation:
        return ProfileFact(
            kind="employer",
            value=segment,
            source_line=line,
            basis="phrase",
            matched_term=sorted(organisation)[0],
        )

    role = words & ROLE_TERMS
    if role:
        # A role phrase naming a subject, an employer, a grade or a seniority
        # level is the post someone holds; a bare one describes their field.
        kind = "occupation" if POST_MARKER_RE.search(segment) else "professional_field"
        return ProfileFact(
            kind=kind,
            value=segment,
            source_line=line,
            basis="phrase",
            matched_term=sorted(role)[0],
        )
    return None


def _phrase_facts(segment: str, line: str) -> list[ProfileFact]:
    """Facts from one unlabelled segment.

    Usually one. "Assistant Professor at Example University" is the exception:
    the author joined a post and an employer with a preposition rather than a
    comma, so both are read, each keeping the words that belong to it.
    """
    cleaned = segment.strip()
    for _ in range(3):
        stripped = ARTICLE_RE.sub("", OPENER_RE.sub("", cleaned)).strip()
        if stripped == cleaned:
            break
        cleaned = stripped

    words = _word_set(cleaned)
    if words & ROLE_TERMS and words & ORG_TERMS:
        split = JOINER_RE.split(cleaned, maxsplit=1)
        if len(split) == 2 and all(part.strip() for part in split):
            left_text, right_text = (part.strip(" .,") for part in split)
            left = _phrase_fact(left_text, line)
            right = _phrase_fact(right_text, line)
            # Only accept the split when it actually separated the two: a
            # single fact means the words did not divide the way it assumed.
            if left is not None and right is not None and right.kind == "employer":
                if left.kind == "professional_field":
                    # The preposition the split consumed is what made this a
                    # post: the phrase named where the role is held.
                    left = replace(left, kind="occupation")
                if left.kind == "occupation":
                    return [left, right]

    single = _phrase_fact(cleaned, line)
    return [single] if single is not None else []


def extract_emails(readme: str) -> list[str]:
    """Addresses the README publishes. Never an address it does not contain."""
    found: list[str] = []
    for value in [*MAILTO_RE.findall(readme), *EMAIL_RE.findall(_visible_text(readme))]:
        cleaned = value.strip().strip(".,;:").lower()
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found[:MAX_EMAILS]


def _visible_text(readme: str) -> str:
    """The README with link and image targets removed, so a URL that merely
    contains an ``@`` cannot be mistaken for a published address."""
    text = MD_IMAGE_RE.sub(" ", readme)
    text = MD_LINK_RE.sub(lambda match: match.group(1), text)
    text = re.sub(r"<[^>]+>", " ", text)
    return BARE_URL_RE.sub(" ", text)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def extract_links(readme: str, *, login: str) -> list[str]:
    """Absolute http(s) links the README publishes.

    The account's own profile and generated badges are dropped: the first is
    where we already are, the second is a picture of a build status.
    """
    candidates = [
        *(match.group(2) for match in MD_LINK_RE.finditer(readme)),
        *HTML_HREF_RE.findall(readme),
        *(match.group(1) for match in BARE_URL_RE.finditer(readme)),
    ]
    own = {f"github.com/{login.lower()}", f"github.com/{login.lower()}/{login.lower()}"}
    links: list[str] = []
    for raw in candidates:
        url = raw.strip().rstrip(".,;)")
        if not url.lower().startswith(("http://", "https://")):
            continue
        host = _host(url)
        if host in BADGE_HOSTS or not host:
            continue
        trimmed = f"{host}{urlsplit(url).path.rstrip('/')}".lower()
        if trimmed in own:
            continue
        if url not in links:
            links.append(url)
        if len(links) >= MAX_LINKS:
            break
    return links


def extract_images(readme: str, *, raw_base: str | None = None) -> list[str]:
    """Image URLs the README publishes, badges excluded.

    A relative path is resolved against the repository's raw base, which is how
    GitHub itself resolves it — a documented rule, not a guess.
    """
    images: list[str] = []
    for raw in [*(m.group(1) for m in MD_IMAGE_RE.finditer(readme)), *HTML_IMG_RE.findall(readme)]:
        url = raw.strip()
        if not url or url.startswith("data:"):
            continue
        if not url.lower().startswith(("http://", "https://")):
            if not raw_base:
                continue
            url = urljoin(raw_base, url.lstrip("./"))
        if _host(url) in BADGE_HOSTS:
            continue
        if url not in images:
            images.append(url)
        if len(images) >= MAX_IMAGES:
            break
    return images


def decode_readme(payload: dict[str, Any]) -> str:
    """Decode the API's base64 README body, refusing anything oversized."""
    encoding = str(payload.get("encoding") or "").lower()
    content = payload.get("content")
    if not isinstance(content, str):
        return ""
    if encoding != "base64":
        return content[:README_MAX_BYTES]
    try:
        decoded = base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError):
        return ""
    return decoded[:README_MAX_BYTES].decode("utf-8", errors="replace")


# --------------------------------------------------------------------- fetch


async def fetch_profile_repository(
    login: str,
    *,
    api: str,
    headers: dict[str, str],
    provider: str,
    timeout: float,  # noqa: ASYNC109 - a per-request budget, not a cancel scope
    retry: RetryPolicy | None = None,
    cache_ttl: int | None = None,
) -> ProfileRepository:
    """Ask whether ``login/login`` exists and is public.

    A missing repository, a private one and a rate limit are three different
    answers and are recorded as three different notes. None of them is retried
    by another route.
    """
    url = f"{api}/repos/{login}/{login}"
    try:
        response = await http.get(
            url,
            provider=provider,
            headers=headers,
            timeout=timeout,
            retry=retry,
            cache_ttl=cache_ttl,
        )
    # Enrichment is an extra; a broken one must never take the run down with it.
    except Exception as exc:
        log.info("github_profile.repository_unreachable", login=login, error=type(exc).__name__)
        return ProfileRepository(
            exists=False,
            note=f"The profile repository could not be checked ({type(exc).__name__}).",
        )

    if response.status_code == 404:
        return ProfileRepository(
            exists=False,
            note=f"@{login} publishes no special profile repository ({login}/{login}).",
        )
    if response.status_code in {403, 429}:
        return ProfileRepository(
            exists=False,
            note=(
                "GitHub rate-limited the profile repository check. Set a read-only "
                "GITHUB_TOKEN to raise the limit, or retry later."
            ),
        )
    if not response.ok:
        return ProfileRepository(
            exists=False,
            note=f"GitHub answered HTTP {response.status_code} for the profile repository.",
        )

    body = response.json()
    if not isinstance(body, dict):
        return ProfileRepository(exists=False, note="GitHub returned an unreadable repository.")
    if body.get("private"):
        return ProfileRepository(
            exists=False,
            note="The profile repository is private. Nothing in it is public evidence.",
        )
    return ProfileRepository(
        exists=True,
        note=f"@{login} publishes the special profile repository {login}/{login}.",
        full_name=str(body.get("full_name") or f"{login}/{login}"),
        html_url=str(body.get("html_url") or f"https://github.com/{login}/{login}"),
        description=str(body.get("description") or "").strip() or None,
        homepage=str(body.get("homepage") or "").strip() or None,
        default_branch=str(body.get("default_branch") or "main"),
    )


async def enrich_login(
    login: str,
    *,
    api: str,
    headers: dict[str, str],
    provider: str,
    timeout: float,  # noqa: ASYNC109 - a per-request budget, not a cancel scope
    retry: RetryPolicy | None = None,
    cache_ttl: int | None = None,
) -> ProfileEnrichment:
    """The profile repository and its README, in at most two documented calls."""
    repository = await fetch_profile_repository(
        login,
        api=api,
        headers=headers,
        provider=provider,
        timeout=timeout,
        retry=retry,
        cache_ttl=cache_ttl,
    )
    enrichment = ProfileEnrichment(login=login, repository=repository)
    if not repository.exists:
        enrichment.readme_note = repository.note
        return enrichment

    try:
        response = await http.get(
            f"{api}/repos/{login}/{login}/readme",
            provider=provider,
            headers=headers,
            timeout=timeout,
            retry=retry,
            cache_ttl=cache_ttl,
        )
    # Enrichment is an extra; a broken one must never take the run down with it.
    except Exception as exc:
        log.info("github_profile.readme_unreachable", login=login, error=type(exc).__name__)
        enrichment.readme_note = f"The profile README could not be read ({type(exc).__name__})."
        return enrichment

    if response.status_code == 404:
        enrichment.readme_note = (
            f"{repository.full_name} exists but publishes no README, so the profile "
            f"page shows nothing."
        )
        return enrichment
    if response.status_code in {403, 429}:
        enrichment.readme_note = (
            "GitHub rate-limited the profile README request. Set a read-only "
            "GITHUB_TOKEN to raise the limit, or retry later."
        )
        return enrichment
    if not response.ok:
        enrichment.readme_note = f"GitHub answered HTTP {response.status_code} for the README."
        return enrichment

    try:
        body = response.json()
    except ValueError:
        enrichment.readme_note = "GitHub returned an unreadable README response."
        return enrichment
    if not isinstance(body, dict):
        enrichment.readme_note = "GitHub returned an unreadable README response."
        return enrichment

    text = decode_readme(body)
    if not text.strip():
        enrichment.readme_note = f"{repository.full_name} publishes an empty README."
        return enrichment

    enrichment.readme_url = str(
        body.get("html_url") or f"{repository.html_url}/blob/{repository.default_branch}/README.md"
    )
    enrichment.readme_sha = str(body.get("sha") or "") or None
    enrichment.facts = extract_facts(text)
    enrichment.emails = extract_emails(text)
    enrichment.links = extract_links(text, login=login)
    enrichment.images = extract_images(
        text,
        raw_base=(
            f"https://raw.githubusercontent.com/{login}/{login}/{repository.default_branch}/"
        ),
    )
    enrichment.readme_note = (
        f"Read the public profile README of {repository.full_name}; "
        f"{len(enrichment.facts)} explicitly stated fact(s) extracted."
    )
    log.info(
        "github_profile.readme_extracted",
        login=login,
        facts=len(enrichment.facts),
        links=len(enrichment.links),
        images=len(enrichment.images),
    )
    return enrichment
