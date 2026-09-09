"""Reading a GitHub profile README, and refusing to read into it.

The extractor's whole value is that it is conservative. So the tests here are
mostly about what it does *not* produce: no claim from a sentence it does not
recognise, no address it was not given, no nationality from a country, and no
identity from a picture.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.collectors.github_profile import (
    MAX_IMAGES,
    MAX_LINKS,
    README_MAX_BYTES,
    decode_readme,
    enrich_login,
    extract_emails,
    extract_facts,
    extract_images,
    extract_links,
    fetch_profile_repository,
)

API = "https://api.github.com"
LOGIN = "example-person"

#: The shape a profile README actually takes: a heading, a one-line
#: self-description, a few labelled links. Documentation domains throughout.
PROFILE_README = """# Hi, I'm Example Person

Lecturer in English (BPS-17), Government of Sindh, Pakistan - Applied Linguist

- Email: e.person@example.edu
- Website: https://example.org/example-person
- [LinkedIn](https://www.linkedin.com/in/example-person)
- [ORCID](https://orcid.org/0000-0002-1825-0097)

![Portrait](https://example.com/portrait.jpg)
![build](https://img.shields.io/badge/build-passing-green)

## Projects

Corpus tooling. Get in touch through the address above.
"""


def _readme_payload(text: str, *, sha: str = "abc123") -> dict:
    return {
        "name": "README.md",
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "html_url": f"https://github.com/{LOGIN}/{LOGIN}/blob/main/README.md",
    }


def _repo_payload(**overrides) -> dict:
    return {
        "full_name": f"{LOGIN}/{LOGIN}",
        "html_url": f"https://github.com/{LOGIN}/{LOGIN}",
        "private": False,
        "description": "My public profile",
        "homepage": "https://example.org/example-person",
        "default_branch": "main",
        **overrides,
    }


def _values(text: str, kind: str) -> list[str]:
    return [fact.value for fact in extract_facts(text) if fact.kind == kind]


# ------------------------------------------------------------------ extraction


def test_an_unlabelled_self_description_yields_each_stated_part():
    facts = {fact.kind: fact.value for fact in extract_facts(PROFILE_README)}
    assert facts["occupation"] == "Lecturer in English (BPS-17)"
    assert facts["employer"] == "Government of Sindh"
    assert facts["professional_field"] == "Applied Linguist"


def test_a_country_is_a_public_association_and_never_a_nationality():
    fact = next(f for f in extract_facts(PROFILE_README) if f.kind == "location")
    assert fact.value == "Pakistan"
    payload = fact.to_dict(source_url="https://example.com/readme")
    assert payload["label"] == "Public geographic association"
    text = " ".join(str(value) for value in payload.values()).lower()
    for forbidden in ("nationality", "citizenship", "citizen", "passport"):
        assert forbidden not in text or "not a claim of" in payload["interpretation"].lower()
    assert "not a claim of nationality" in payload["interpretation"].lower()


def test_no_extracted_fact_is_ever_typed_as_a_nationality():
    """The kind does not exist, so no code path can produce one."""
    from app.collectors.github_profile import KIND_LABELS

    assert "nationality" not in KIND_LABELS
    assert "citizenship" not in KIND_LABELS


def test_every_claim_keeps_the_line_that_states_it():
    for fact in extract_facts(PROFILE_README):
        assert fact.source_line, f"{fact.kind} lost its source line"
        assert fact.value in fact.source_line or fact.basis == "labelled"


def test_a_labelled_field_is_read_wherever_it_appears():
    text = "# Title\n\n" + "\n".join(f"line {n}" for n in range(60)) + "\n\nOccupation: Archivist\n"
    assert _values(text, "occupation") == ["Archivist"]


def test_prose_far_below_the_header_never_becomes_a_claim():
    text = "# Title\n\n" + "\n".join(f"line {n}" for n in range(60)) + "\n\nEngineer\n"
    assert _values(text, "professional_field") == []


def test_a_post_is_told_from_a_field_by_what_the_phrase_says():
    # A subject, an employer, a grade or a seniority level makes it a post.
    assert _values("Lecturer in English", "occupation") == ["Lecturer in English"]
    assert _values("Senior Engineer", "occupation") == ["Senior Engineer"]
    # A bare descriptor is what someone does, not the post they hold.
    assert _values("Applied Linguist", "professional_field") == ["Applied Linguist"]


def test_a_post_joined_to_its_employer_yields_both():
    facts = {fact.kind: fact.value for fact in extract_facts("Professor at Example University")}
    assert facts["occupation"] == "Professor"
    assert facts["employer"] == "Example University"


def test_an_unrecognised_sentence_produces_nothing():
    assert extract_facts("I like building things and writing about them at length.") == []


def test_a_malformed_readme_is_handled_without_raising():
    for text in ("", "   ", "```\nunclosed", "|||", "\x00\x01\x02", "# " + "x" * 5000):
        assert isinstance(extract_facts(text), list)
        assert isinstance(extract_emails(text), list)
        assert isinstance(extract_links(text, login=LOGIN), list)


# --------------------------------------------------------------------- emails


def test_an_email_is_read_only_when_the_readme_prints_one():
    assert extract_emails(PROFILE_README) == ["e.person@example.edu"]


def test_no_address_is_constructed_from_a_name_and_a_domain():
    text = "# Example Person\n\nLecturer, Example University, https://example.edu\n"
    assert extract_emails(text) == []


def test_an_at_sign_inside_a_url_is_not_an_address():
    assert extract_emails("See <https://example.com/u/@handle/posts>") == []


# ---------------------------------------------------------------------- links


def test_links_are_read_and_badges_dropped():
    links = extract_links(PROFILE_README, login=LOGIN)
    assert "https://www.linkedin.com/in/example-person" in links
    assert "https://example.org/example-person" in links
    assert not any("shields.io" in link for link in links)


def test_the_accounts_own_profile_is_not_a_link_to_itself():
    text = f"[me](https://github.com/{LOGIN}) and [repo](https://github.com/{LOGIN}/{LOGIN})"
    assert extract_links(text, login=LOGIN) == []


def test_links_are_capped():
    text = "\n".join(f"[l{n}](https://example.com/{n})" for n in range(80))
    assert len(extract_links(text, login=LOGIN)) == MAX_LINKS


# --------------------------------------------------------------------- images


def test_images_are_read_and_generated_badges_dropped():
    images = extract_images(PROFILE_README)
    assert images == ["https://example.com/portrait.jpg"]


def test_a_relative_image_resolves_the_way_github_resolves_it():
    base = f"https://raw.githubusercontent.com/{LOGIN}/{LOGIN}/main/"
    assert extract_images("![me](assets/me.png)", raw_base=base) == [
        f"https://raw.githubusercontent.com/{LOGIN}/{LOGIN}/main/assets/me.png"
    ]


def test_a_relative_image_is_dropped_when_there_is_no_base_to_resolve_against():
    assert extract_images("![me](assets/me.png)") == []


def test_images_are_capped():
    text = "\n".join(f"![i{n}](https://example.com/{n}.png)" for n in range(40))
    assert len(extract_images(text)) == MAX_IMAGES


# -------------------------------------------------------------------- decoding


def test_an_oversized_readme_is_truncated_rather_than_read_whole():
    payload = _readme_payload("x" * (README_MAX_BYTES * 2))
    assert len(decode_readme(payload)) <= README_MAX_BYTES


def test_undecodable_content_yields_nothing_rather_than_raising():
    assert decode_readme({"encoding": "base64", "content": "!!!not base64!!!"}) == ""
    assert decode_readme({"encoding": "base64"}) == ""


# ----------------------------------------------------------------- retrieval


@respx.mock
async def test_the_profile_repository_is_found_through_the_api(mock_http):
    route = respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    repository = await fetch_profile_repository(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert repository.exists
    assert repository.full_name == f"{LOGIN}/{LOGIN}"
    assert repository.homepage == "https://example.org/example-person"
    # The documented API, never a scrape of the rendered page.
    assert route.calls[0].request.url.host == "api.github.com"


@respx.mock
async def test_a_missing_profile_repository_is_reported_honestly(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(return_value=httpx.Response(404))
    repository = await fetch_profile_repository(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not repository.exists
    assert "publishes no special profile repository" in repository.note


@respx.mock
async def test_a_private_profile_repository_is_not_public_evidence(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload(private=True))
    )
    repository = await fetch_profile_repository(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not repository.exists
    assert "private" in repository.note.lower()


@respx.mock
async def test_a_rate_limit_says_so_instead_of_pretending_there_is_nothing(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(return_value=httpx.Response(403))
    repository = await fetch_profile_repository(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not repository.exists
    assert "rate-limited" in repository.note


@respx.mock
async def test_enrichment_reads_the_readme_and_keeps_its_provenance(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(200, json=_readme_payload(PROFILE_README))
    )
    enrichment = await enrich_login(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert enrichment.retrieved
    assert enrichment.readme_url.endswith("/blob/main/README.md")
    assert enrichment.readme_sha == "abc123"
    assert enrichment.values("occupation") == ["Lecturer in English (BPS-17)"]
    payload = enrichment.to_dict()
    assert all(fact["source_url"] == enrichment.readme_url for fact in payload["readme_facts"])


@respx.mock
async def test_enrichment_makes_exactly_two_calls_and_lists_no_repositories(mock_http):
    """Profile enrichment, not a GitHub crawl."""
    repo = respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    readme = respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(200, json=_readme_payload(PROFILE_README))
    )
    listing = respx.get(f"{API}/users/{LOGIN}/repos").mock(
        return_value=httpx.Response(200, json=[])
    )
    await enrich_login(LOGIN, api=API, headers={}, provider="github_people", timeout=5.0)
    assert repo.call_count == 1
    assert readme.call_count == 1
    assert listing.call_count == 0, "enrichment must never enumerate repositories"


@respx.mock
async def test_a_repository_without_a_readme_is_reported_rather_than_guessed(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(return_value=httpx.Response(404))
    enrichment = await enrich_login(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not enrichment.retrieved
    assert "no README" in enrichment.readme_note
    assert enrichment.facts == []


@respx.mock
async def test_an_unreadable_readme_response_does_not_raise(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    enrichment = await enrich_login(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not enrichment.retrieved
    assert "unreadable" in enrichment.readme_note


@respx.mock
async def test_a_transport_failure_is_absorbed(mock_http):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(side_effect=httpx.ConnectError("refused"))
    enrichment = await enrich_login(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not enrichment.retrieved
    assert "could not be checked" in enrichment.readme_note


@pytest.mark.parametrize("status", [403, 429, 500])
@respx.mock
async def test_every_unhappy_status_leaves_an_explanation(mock_http, status):
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}").mock(
        return_value=httpx.Response(200, json=_repo_payload())
    )
    respx.get(f"{API}/repos/{LOGIN}/{LOGIN}/readme").mock(return_value=httpx.Response(status))
    enrichment = await enrich_login(
        LOGIN, api=API, headers={}, provider="github_people", timeout=5.0
    )
    assert not enrichment.retrieved
    assert enrichment.readme_note
