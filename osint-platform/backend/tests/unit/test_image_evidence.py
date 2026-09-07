"""Image evidence: hashing, honest non-fetching, SSRF, and the biometric boundary.

The boundary that matters most is the one this module cannot cross. There is no
facial recognition, no embedding, no comparison of two images — and the tests
below assert the absence, because a limit nobody checks is a limit that erodes.
"""

from __future__ import annotations

import hashlib
import struct

import httpx
import pytest
import respx

from app.models import ImageFetchState
from app.services.images import IMAGE_CONTENT_TYPES, NO_ANALYSIS, fetch_image, image_dimensions

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + struct.pack(">I", 13)
    + b"IHDR"
    + struct.pack(">II", 640, 480)
    + b"\x08\x02\x00\x00\x00"
    + b"\x00" * 8
)
GIF = b"GIF89a" + struct.pack("<HH", 320, 200) + b"\x00" * 16
JPEG = (
    b"\xff\xd8\xff\xe0"
    + struct.pack(">H", 16)
    + b"JFIF\x00"
    + b"\x00" * 10
    + b"\xff\xc0"
    + struct.pack(">H", 17)
    + b"\x08"
    + struct.pack(">HH", 300, 400)
    + b"\x03"
    + b"\x00" * 9
)


# ------------------------------------------------------------------ dimensions


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(PNG, (640, 480)), (GIF, (320, 200)), (JPEG, (400, 300))],
)
def test_dimensions_are_read_from_the_header(payload, expected):
    assert image_dimensions(payload) == expected


def test_an_unrecognised_format_returns_no_dimensions_rather_than_a_guess():
    assert image_dimensions(b"not an image at all, really quite definitely not") == (None, None)


def test_a_truncated_file_does_not_raise():
    assert image_dimensions(b"\x89PNG") == (None, None)


# ---------------------------------------------------------------------- fetch


@respx.mock
async def test_a_fetched_image_is_hashed_and_measured():
    respx.get("https://example.org/photo.png").mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    result = await fetch_image("https://example.org/photo.png")
    assert result["fetch_state"] is ImageFetchState.FETCHED
    assert result["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result["byte_length"] == len(PNG)
    assert (result["width"], result["height"]) == (640, 480)
    assert result["content_type"] == "image/png"


@respx.mock
async def test_a_refused_image_is_reference_only_and_carries_no_hash():
    """A hash for bytes we never read would be a fabricated guarantee."""
    respx.get("https://example.org/blocked.png").mock(return_value=httpx.Response(403))
    result = await fetch_image("https://example.org/blocked.png")
    assert result["fetch_state"] is ImageFetchState.REFERENCE_ONLY
    assert result["sha256"] is None
    assert "403" in result["fetch_note"]


@respx.mock
async def test_a_non_image_response_is_not_recorded_as_an_image():
    respx.get("https://example.org/login.html").mock(
        return_value=httpx.Response(
            200, content=b"<html>login</html>", headers={"content-type": "text/html"}
        )
    )
    result = await fetch_image("https://example.org/login.html")
    assert result["fetch_state"] is ImageFetchState.REFERENCE_ONLY
    assert result["sha256"] is None
    assert "not an image" in result["fetch_note"]


@respx.mock
async def test_a_transport_failure_is_recorded_not_raised():
    respx.get("https://example.org/gone.png").mock(side_effect=httpx.ConnectError("refused"))
    result = await fetch_image("https://example.org/gone.png")
    assert result["fetch_state"] is ImageFetchState.REFERENCE_ONLY
    assert "could not be fetched" in result["fetch_note"]


# ----------------------------------------------------------------------- SSRF


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/photo.png",
        "http://localhost/photo.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://10.0.0.5/photo.png",
        "http://192.168.1.10/photo.png",
        "http://[::1]/photo.png",
    ],
)
async def test_unsafe_image_urls_are_blocked_before_any_connection(url):
    result = await fetch_image(url)
    assert result["fetch_state"] is ImageFetchState.BLOCKED
    assert "SSRF" in result["fetch_note"]
    assert result["sha256"] is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x.png", "gopher://x/1"])
async def test_non_http_schemes_are_blocked(url):
    result = await fetch_image(url)
    assert result["fetch_state"] is ImageFetchState.BLOCKED


# ------------------------------------------------------- the biometric limit


def test_every_image_record_states_that_no_analysis_was_performed():
    assert NO_ANALYSIS["analysis"] == "none"
    assert NO_ANALYSIS["biometric_matching"] is False
    assert NO_ANALYSIS["facial_recognition"] is False
    assert "no facial recognition" in NO_ANALYSIS["interpretation"].lower()


def test_the_image_module_exposes_no_comparison_or_recognition_function():
    """A limit nobody checks is a limit that erodes."""
    import app.services.images as module

    names = [name.lower() for name in dir(module)]
    forbidden_names = (
        "compare",
        "similarity",
        "embedding",
        "face",
        "recognise",
        "recognize",
        "match",
    )
    for forbidden in forbidden_names:
        assert not any(forbidden in name for name in names), f"{forbidden} appeared in the API"


def test_only_image_content_types_are_accepted():
    assert all(item.startswith("image/") for item in IMAGE_CONTENT_TYPES)
