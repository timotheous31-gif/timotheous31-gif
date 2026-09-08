"""Public image evidence: fetching, hashing and honest non-fetching.

An image here is **page context**. What it can support is "this picture appears
on a page that names the subject". What it can never support is any claim about
who is depicted, because the platform performs no facial recognition, no
biometric embedding, and no similarity comparison of any kind. There is no code
in this module that compares two images, and no column in the schema that could
hold such a result.

Fetching reuses :mod:`app.core.http`, which runs the SSRF guard on the initial
URL *and* on every redirect hop. No second HTTP path exists, deliberately: a
"just for images" fetcher is exactly how a hardened codebase grows an unguarded
one.
"""

from __future__ import annotations

import hashlib
import struct
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import http
from app.core.errors import SSRFError
from app.core.logging import get_logger
from app.core.ssrf import validate_url
from app.models import Evidence, ImageEvidence, ImageFetchState
from app.services.evidence import EvidenceStore

log = get_logger(__name__)

#: Image bytes are read only up to this size. A profile picture is small; a
#: multi-megabyte "image" is either not one or not worth the memory.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Content types we are willing to treat as an image.
IMAGE_CONTENT_TYPES = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/avif",
    "image/svg+xml",
)

#: Stated on every record. Not a comment — a value that travels with the data.
NO_ANALYSIS: dict[str, Any] = {
    "analysis": "none",
    "biometric_matching": False,
    "facial_recognition": False,
    "interpretation": (
        "Context evidence only: this image appears on a public page associated "
        "with the candidate. The platform performs no facial recognition or "
        "biometric analysis and makes no claim about who is depicted."
    ),
}


def image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    """Width and height read from the file header.

    Headers only, deliberately: decoding an untrusted image with a full imaging
    library to learn two integers would import a large attack surface for very
    little. Unrecognised formats return ``(None, None)`` rather than a guess.
    """
    if len(payload) < 24:
        return (None, None)

    if payload.startswith(b"\x89PNG\r\n\x1a\n") and payload[12:16] == b"IHDR":
        width, height = struct.unpack(">II", payload[16:24])
        return (int(width), int(height))

    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        width, height = struct.unpack("<HH", payload[6:10])
        return (int(width), int(height))

    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        chunk = payload[12:16]
        if chunk == b"VP8 " and len(payload) >= 30:
            return (
                int(struct.unpack("<H", payload[26:28])[0] & 0x3FFF),
                int(struct.unpack("<H", payload[28:30])[0] & 0x3FFF),
            )
        if chunk == b"VP8L" and len(payload) >= 25:
            bits = int.from_bytes(payload[21:25], "little")
            return (int((bits & 0x3FFF) + 1), int(((bits >> 14) & 0x3FFF) + 1))
        if chunk == b"VP8X" and len(payload) >= 30:
            return (
                int.from_bytes(payload[24:27], "little") + 1,
                int.from_bytes(payload[27:30], "little") + 1,
            )
        return (None, None)

    if payload.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(payload)

    return (None, None)


def _jpeg_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    """Walk JPEG segments to the frame header."""
    index = 2
    size = len(payload)
    while index + 9 < size:
        if payload[index] != 0xFF:
            index += 1
            continue
        marker = payload[index + 1]
        # Start-of-frame markers carry the dimensions; SOF4/SOF8/SOF12 do not.
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            height, width = struct.unpack(">HH", payload[index + 5 : index + 9])
            return (int(width), int(height))
        if index + 4 > size:
            break
        segment = struct.unpack(">H", payload[index + 2 : index + 4])[0]
        if segment <= 0:
            break
        index += 2 + segment
    return (None, None)


async def fetch_image(url: str, *, provider: str = "image_evidence") -> dict[str, Any]:
    """Fetch image bytes through the platform's guarded HTTP path.

    Returns a record of what happened — never raises for an unreachable or
    refused image. A blocked fetch is a fact to record, not an error to hide,
    and the caller stores it as REFERENCE_ONLY or BLOCKED accordingly.
    """
    outcome: dict[str, Any] = {
        "fetch_state": ImageFetchState.REFERENCE_ONLY,
        "sha256": None,
        "content_type": None,
        "byte_length": None,
        "width": None,
        "height": None,
        "redirects": [],
        "final_url": None,
        "fetch_note": "",
    }
    try:
        # Refuse before any connection is opened, so an unsafe address is never
        # dialled even once.
        validate_url(url)
    except SSRFError as exc:
        outcome["fetch_state"] = ImageFetchState.BLOCKED
        outcome["fetch_note"] = f"Refused by the SSRF guard: {exc}"
        return outcome

    try:
        response = await http.get(url, provider=provider, max_bytes=MAX_IMAGE_BYTES)
    except SSRFError as exc:
        # A redirect hop pointed somewhere unsafe.
        outcome["fetch_state"] = ImageFetchState.BLOCKED
        outcome["fetch_note"] = f"A redirect was refused by the SSRF guard: {exc}"
        return outcome
    except Exception as exc:
        outcome["fetch_note"] = (
            f"The image could not be fetched ({type(exc).__name__}: {exc}). "
            f"The URL is recorded; nothing was downloaded."
        )
        return outcome

    outcome["redirects"] = list(response.redirects)
    outcome["final_url"] = response.final_url
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    outcome["content_type"] = content_type or None

    if response.status_code >= 400:
        outcome["fetch_note"] = (
            f"The host answered {response.status_code}. The URL is recorded; "
            f"nothing was downloaded."
        )
        return outcome

    if content_type and not content_type.startswith("image/"):
        outcome["fetch_note"] = (
            f"The URL returned {content_type!r}, not an image. Recorded as a "
            f"reference without content."
        )
        return outcome

    payload = response.content
    outcome["sha256"] = hashlib.sha256(payload).hexdigest()
    outcome["byte_length"] = len(payload)
    width, height = image_dimensions(payload)
    outcome["width"] = width
    outcome["height"] = height
    outcome["fetch_state"] = ImageFetchState.FETCHED
    return outcome


def record_image(
    session: Session,
    *,
    case_id: uuid.UUID,
    image_url: str,
    source_page_url: str,
    origin: str,
    evidence_class: str,
    platform: str | None = None,
    caption: str | None = None,
    context_text: str | None = None,
    candidate_entity_id: uuid.UUID | None = None,
    social_profile_id: uuid.UUID | None = None,
    fetch: dict[str, Any] | None = None,
    retrieved_at: datetime | None = None,
) -> ImageEvidence:
    """Persist one public image, fetched or not.

    Deduplicated on (image_url, source_page_url) within the case: the same
    picture on two pages is two pieces of evidence, because the page is what
    gives it meaning.
    """
    moment = retrieved_at or datetime.now(UTC)
    result = fetch or {}
    state = result.get("fetch_state", ImageFetchState.REFERENCE_ONLY)

    existing = session.scalar(
        select(ImageEvidence).where(
            ImageEvidence.case_id == case_id,
            ImageEvidence.image_url == image_url,
            ImageEvidence.source_page_url == source_page_url,
        )
    )
    if existing is not None:
        # Re-importing must not silently drop an attribution that was made.
        if candidate_entity_id and existing.candidate_entity_id is None:
            existing.candidate_entity_id = candidate_entity_id
        if social_profile_id and existing.social_profile_id is None:
            existing.social_profile_id = social_profile_id
        session.flush()
        return existing

    record = ImageEvidence(
        case_id=case_id,
        candidate_entity_id=candidate_entity_id,
        social_profile_id=social_profile_id,
        image_url=image_url,
        source_page_url=source_page_url,
        platform=platform,
        caption=caption,
        context_text=context_text,
        fetch_state=state,
        # Only a fetched image carries a hash. Claiming one for a URL we never
        # read would be a fabricated integrity guarantee.
        sha256=result.get("sha256") if state is ImageFetchState.FETCHED else None,
        content_type=result.get("content_type"),
        byte_length=result.get("byte_length"),
        width=result.get("width"),
        height=result.get("height"),
        redirects=list(result.get("redirects") or []),
        final_url=result.get("final_url"),
        fetch_note=result.get("fetch_note") or None,
        origin=origin,
        evidence_class=evidence_class,
        retrieved_at=moment,
        attributes=dict(NO_ANALYSIS),
    )
    session.add(record)
    session.flush()

    # A provenance descriptor goes into the existing evidence store. The image
    # bytes themselves are not persisted: the hash plus the descriptor give the
    # integrity guarantee, and re-hosting someone's photograph is a liability
    # this platform has no reason to take on.
    stored = EvidenceStore().store(
        session,
        case_id=case_id,
        collector=origin,
        source_url=source_page_url,
        content={
            "image_url": image_url,
            "source_page_url": source_page_url,
            "final_url": result.get("final_url"),
            "redirects": list(result.get("redirects") or []),
            "fetch_state": str(state),
            "sha256_of_image_bytes": record.sha256,
            "content_type": result.get("content_type"),
            "byte_length": result.get("byte_length"),
            "dimensions": {"width": result.get("width"), "height": result.get("height")},
            "retrieved_at": moment.isoformat(),
            "caption": caption,
            **NO_ANALYSIS,
        },
        retrieved_at=moment,
    )
    record.evidence_id = stored.evidence.id
    session.flush()
    log.info(
        "image.recorded",
        case_id=str(case_id),
        fetch_state=str(state),
        hashed=bool(record.sha256),
        platform=platform,
    )
    return record


def images_for_case(
    session: Session, case_id: uuid.UUID, *, candidate_entity_id: uuid.UUID | None = None
) -> list[ImageEvidence]:
    """Every image in the case, newest first, optionally for one candidate."""
    query = select(ImageEvidence).where(ImageEvidence.case_id == case_id)
    if candidate_entity_id is not None:
        query = query.where(ImageEvidence.candidate_entity_id == candidate_entity_id)
    return list(session.scalars(query.order_by(ImageEvidence.created_at.desc())))


def evidence_for(session: Session, record: ImageEvidence) -> Evidence | None:
    """The stored provenance descriptor for an image, when there is one."""
    if record.evidence_id is None:
        return None
    return session.get(Evidence, record.evidence_id)
