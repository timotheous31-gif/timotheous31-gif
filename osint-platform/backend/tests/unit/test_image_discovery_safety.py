"""Public images: fetched safely, recorded honestly, never identifying.

An image here establishes that a picture appears on a public page associated
with a candidate. It establishes nothing about who is in it, and there is no
code path anywhere that could form such an opinion.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib

import httpx
import pytest
import respx

from app.models.enums import ImageFetchState
from app.reporting.model import IMAGE_DISCLAIMER, render_safety
from app.services.images import fetch_image

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "app"

#: A 1x1 PNG, so dimensions and a hash are real rather than asserted.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


# ---------------------------------------------------------------- fetching


@respx.mock
async def test_a_fetched_image_carries_a_hash_of_the_bytes_that_were_read(mock_http):
    respx.get("https://example.com/portrait.png").mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    result = await fetch_image("https://example.com/portrait.png")
    assert result["fetch_state"] is ImageFetchState.FETCHED
    assert result["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result["content_type"] == "image/png"
    assert result["byte_length"] == len(PNG)
    assert (result["width"], result["height"]) == (1, 1)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/portrait.png",
        "http://localhost/portrait.png",
        "http://10.0.0.5/portrait.png",
        "http://192.168.1.10/portrait.png",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "gopher://example.com/portrait.png",
    ],
)
async def test_an_unsafe_image_url_is_refused(url, mock_http):
    """One guarded fetcher, so an image URL is held to the same standard.

    A refusal is *recorded*, not raised: a blocked fetch is a fact about the
    evidence, and losing the run over it would lose everything else collected
    with it. What must never happen is bytes coming back.
    """
    result = await fetch_image(url)
    assert result["fetch_state"] is ImageFetchState.BLOCKED, url
    assert result["sha256"] is None
    assert result["byte_length"] is None
    assert "refused" in result["fetch_note"].lower()


@respx.mock
async def test_a_redirect_to_a_private_address_is_refused_mid_flight(mock_http):
    """Every hop is re-validated, not just the URL that was handed in."""
    respx.get("https://example.com/portrait.png").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    result = await fetch_image("https://example.com/portrait.png")
    assert result["fetch_state"] is ImageFetchState.BLOCKED
    assert result["sha256"] is None
    assert "redirect" in result["fetch_note"].lower()


@respx.mock
async def test_a_non_image_response_is_not_recorded_as_an_image(mock_http):
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    result = await fetch_image("https://example.com/page")
    assert result["fetch_state"] is not ImageFetchState.FETCHED
    assert result["sha256"] is None


# --------------------------------------------------------- render safety


@pytest.mark.parametrize(
    "url,safe",
    [
        ("https://avatars.githubusercontent.com/u/1?v=4", True),
        ("https://example.com/portrait.jpg", True),
        ("http://example.com/portrait.jpg", False),
        ("https://127.0.0.1/portrait.jpg", False),
        ("https://169.254.169.254/portrait.jpg", False),
        ("https://user:pass@example.com/portrait.jpg", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_render_safety_decides_what_a_viewer_may_be_pointed_at(url, safe):
    allowed, note = render_safety(url)
    assert allowed is safe
    assert bool(note) is not safe, "a refusal must say why, and an allowance must not"


def test_render_safety_does_no_dns_lookup(monkeypatch):
    """Building a report must not resolve a hostname per image."""
    import socket

    def explode(*_args, **_kwargs):
        raise AssertionError("render safety must not resolve anything")

    monkeypatch.setattr(socket, "getaddrinfo", explode)
    assert render_safety("https://example.com/portrait.jpg")[0] is True


# ------------------------------------------------------- never an identity


def test_the_disclaimer_travels_with_every_image():
    assert "does not independently establish identity" in IMAGE_DISCLAIMER


def test_no_module_compares_two_images():
    """There is no similarity code, so there is nothing to switch on later.

    Matched against *identifiers* rather than raw text: ``images.py`` says in
    prose that it computes no biometric embedding, and a naive grep flags the
    promise as the offence. Names, attributes and imports are what would
    actually do the thing.
    """
    banned = {
        "face_encoding",
        "face_encodings",
        "face_locations",
        "face_distance",
        "compare_faces",
        "embedding",
        "embeddings",
        "cosine_similarity",
        "phash",
        "dhash",
        "ahash",
        "perceptual_hash",
        "image_similarity",
        "reverse_image_search",
    }
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.alias):
                names.append(node.name.rsplit(".", 1)[-1])
            for name in names:
                if name.lower() in banned:
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, offenders


def test_the_image_record_has_no_field_that_could_hold_a_match():
    from app.models import ImageEvidence

    columns = {column.name for column in ImageEvidence.__table__.columns}
    for forbidden in ("face", "embedding", "similarity", "match_score", "identity", "person_id"):
        assert not any(forbidden in name for name in columns), forbidden
