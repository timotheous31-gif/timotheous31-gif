"""The manual search recon workflow, end to end over the API.

The workflow exists because the platform will not scrape a search engine. So the
properties worth defending are about honesty and safety: an imported result must
say a human imported it, must carry the query that produced it, and must not be
able to point the backend at anything private.
"""

from __future__ import annotations

import pytest

NAME = "Timotheous Samar"


async def _person_target(api_client, case_id, **context):
    payload = {"value": NAME, "type": "PERSON"}
    if context:
        payload["context"] = context
    response = await api_client.post(f"/api/v1/cases/{case_id}/targets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _result(**overrides):
    base = {
        "query": f'"{NAME}" site:linkedin.com',
        "url": "https://www.linkedin.com/in/example-person",
        "title": "Example Person — LinkedIn",
        "snippet": "Researcher at Example University",
        "engine": "Google",
    }
    return {**base, **overrides}


# ------------------------------------------------------------- query generation


async def test_recon_queries_are_offered_for_a_person_target(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    assert response.status_code == 200
    body = response.json()
    assert body["subject_name"] == NAME
    assert any(item["query"] == f'"{NAME}"' for item in body["queries"])
    # The plan states plainly that the platform will not run these itself.
    assert "does not submit" in body["execution"]
    assert "scrape" in body["execution"]


async def test_supplied_anchors_appear_in_the_generated_queries(api_client, case_id):
    target_id = await _person_target(
        api_client, case_id, organizations=["Example University"], known_usernames=["octocat"]
    )
    response = await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    queries = [item["query"] for item in response.json()["queries"]]
    assert f'"{NAME}" "Example University"' in queries
    assert f'"{NAME}" "octocat"' in queries


async def test_recon_queries_are_refused_for_a_non_person_target(api_client, case_id):
    created = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"}
    )
    target_id = created.json()["id"]
    response = await api_client.get(f"/api/v1/cases/{case_id}/targets/{target_id}/recon-queries")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# ------------------------------------------------------------------- importing


async def test_an_imported_result_keeps_its_query_and_engine(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result()]},
    )
    assert response.status_code == 201, response.text
    imported = response.json()[0]
    assert imported["query"] == f'"{NAME}" site:linkedin.com'
    assert imported["engine"] == "Google"
    assert imported["platform"] == "linkedin"
    assert imported["url_kind"] == "social"
    assert imported["handle"] == "example-person"


async def test_an_imported_result_is_never_presented_as_platform_fetched(api_client, case_id):
    """The provenance distinction the whole workflow rests on."""
    target_id = await _person_target(api_client, case_id)
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result()]},
    )
    findings = await api_client.get(f"/api/v1/cases/{case_id}/findings")
    finding = findings.json()["items"][0]

    assert finding["collector"] == "manual_search_recon"
    assert finding["collector"] != "search"
    assert finding["data"]["evidence_class"] == "investigator_imported"
    assert any("Imported by the investigator" in r for r in finding["confidence_reasons"])
    assert any("the platform did not" in r for r in finding["confidence_reasons"])


async def test_an_imported_result_is_hashed_into_the_evidence_store(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result()]},
    )
    assert response.json()[0]["evidence_sha256"], "no artefact hash recorded"

    verification = await api_client.get(f"/api/v1/cases/{case_id}/evidence/verify")
    assert verification.json()["intact"] is True


async def test_importing_the_same_url_twice_does_not_duplicate_it(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    for _ in range(2):
        await api_client.post(
            f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
            json={"results": [_result()]},
        )
    listing = await api_client.get(f"/api/v1/cases/{case_id}/recon-results")
    assert len(listing.json()) == 1


async def test_imports_are_refused_for_a_non_person_target(api_client, case_id):
    created = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"}
    )
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{created.json()['id']}/recon-results",
        json={"results": [_result()]},
    )
    assert response.status_code == 422


# --------------------------------------------------------------- image evidence


async def test_an_imported_image_is_stored_as_context_evidence(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                _result(
                    url="https://example.org/conference/speakers",
                    image_url="https://example.org/img/speaker.jpg",
                    caption="Speakers at the 2024 Example Conference",
                    query=f'"{NAME}" conference',
                )
            ]
        },
    )
    assert response.status_code == 201
    imported = response.json()[0]
    assert imported["is_image"] is True
    assert imported["image_url"] == "https://example.org/img/speaker.jpg"
    assert imported["caption"] == "Speakers at the 2024 Example Conference"


async def test_image_evidence_states_that_no_biometric_analysis_occurred(api_client, case_id):
    """The one thing a reader might otherwise assume, said on the record."""
    target_id = await _person_target(api_client, case_id)
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                _result(
                    url="https://example.org/page",
                    image_url="https://example.org/img/photo.jpg",
                )
            ]
        },
    )
    findings = await api_client.get(
        f"/api/v1/cases/{case_id}/findings", params={"kind": "IMAGE_EVIDENCE"}
    )
    data = findings.json()["items"][0]["data"]
    assert data["biometric_matching"] is False
    assert data["analysis"] == "none"
    assert "no facial recognition" in data["interpretation"]
    assert "makes no claim" in data["interpretation"]


async def test_images_can_be_listed_on_their_own(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                _result(url="https://example.org/a"),
                _result(url="https://example.org/b", image_url="https://example.org/b.jpg"),
            ]
        },
    )
    everything = await api_client.get(f"/api/v1/cases/{case_id}/recon-results")
    images = await api_client.get(
        f"/api/v1/cases/{case_id}/recon-results", params={"images_only": True}
    )
    assert len(everything.json()) == 2
    assert len(images.json()) == 1
    assert images.json()[0]["is_image"] is True


# ------------------------------------------------------------------ URL safety


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8000/internal",
        "http://[::1]/internal",
        "http://10.0.0.5/private",
        "http://192.168.1.1/router",
        "http://172.16.0.1/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
async def test_private_and_metadata_urls_are_refused(api_client, case_id, url):
    """A pasted URL is held to the same SSRF standard as a discovered one."""
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result(url=url)]},
    )
    assert response.status_code in {400, 422}, response.text
    assert response.json()["code"] in {"ssrf_blocked", "validation_error"}


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/file", "file:///etc/passwd", "javascript:alert(1)"],
)
async def test_non_http_schemes_are_refused(api_client, case_id, url):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result(url=url)]},
    )
    assert response.status_code in {400, 422}


async def test_urls_carrying_credentials_are_refused(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result(url="https://user:secret@example.com/page")]},
    )
    assert response.status_code in {400, 422}
    assert "credentials" in response.json()["message"].lower()


async def test_a_private_image_url_is_refused_even_when_the_page_is_public(api_client, case_id):
    """Every URL on an import is checked, not just the first one."""
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={
            "results": [
                _result(
                    url="https://example.org/page",
                    image_url="http://169.254.169.254/latest/meta-data/",
                )
            ]
        },
    )
    assert response.status_code in {400, 422}


async def test_a_fragment_is_stripped_from_a_stored_url(api_client, case_id):
    target_id = await _person_target(api_client, case_id)
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/targets/{target_id}/recon-results",
        json={"results": [_result(url="https://example.org/page#section-2")]},
    )
    assert response.json()[0]["url"] == "https://example.org/page"
