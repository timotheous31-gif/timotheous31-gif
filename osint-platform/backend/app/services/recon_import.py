"""Importing public search results an investigator found themselves.

This is the second half of the zero-cost recon workflow. The platform generates
queries; the investigator runs them in their own browser; and whatever public
results they judge relevant come back here.

The design constraint that matters most is provenance. A result imported this
way was *not* fetched by the platform from a search API, and recording it as
though it were would misstate how the evidence was obtained — the difference
between "a provider returned this" and "a person chose this" is exactly what a
reviewer needs to weigh it. So imported results carry their own collector
identity, their own evidence class, and the query and engine that produced them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.social import classify_url
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.ssrf import validate_url
from app.models import Case, Finding, Target
from app.models.enums import Classification, FindingKind, TargetType
from app.schemas.recon import ManualResultImport
from app.services.evidence import EvidenceStore

log = get_logger(__name__)

#: The collector identity imported results are filed under. Deliberately not
#: "search": that name belongs to the paid API collector, and a report must be
#: able to tell the two apart at a glance.
MANUAL_COLLECTOR = "manual_search_recon"
MANUAL_SOURCE_LABEL = "Manual search recon"

#: Evidence classes. Reports group by these because they are not equally strong.
EVIDENCE_API_FETCHED = "api_fetched"
EVIDENCE_PAGE_FETCHED = "page_fetched"
EVIDENCE_INVESTIGATOR_IMPORTED = "investigator_imported"


def import_results(
    session: Session,
    case_id: uuid.UUID,
    target_id: uuid.UUID,
    payload: ManualResultImport,
) -> list[Finding]:
    """Store investigator-imported public results against a PERSON target.

    Raises:
        NotFoundError: the case or target does not exist.
        ValidationError: the target is not a PERSON, or a URL is not a safe
            public http(s) address.
    """
    case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} does not exist")
    target = session.get(Target, target_id)
    if target is None or target.case_id != case_id:
        raise NotFoundError(f"Target {target_id} is not in case {case_id}")
    if target.type is not TargetType.PERSON:
        raise ValidationError(
            "Manual search results can only be imported against a PERSON target",
            detail={"target_type": str(target.type)},
        )

    store = EvidenceStore()
    imported_at = datetime.now(UTC)
    findings: list[Finding] = []

    for item in payload.results:
        url = _validated_public_url(item.url)
        image_url = _validated_public_url(item.image_url) if item.image_url else None
        thumbnail_url = _validated_public_url(item.thumbnail_url) if item.thumbnail_url else None
        is_image = bool(image_url or thumbnail_url)

        profile = classify_url(url)
        data: dict[str, Any] = {
            "url": url,
            "host": (profile.platform if profile else ""),
            "title": item.title,
            "snippet": item.snippet,
            "query": item.query,
            "engine": item.engine,
            "result_type": item.result_type,
            "notes": item.notes,
            "imported_at": imported_at.isoformat(),
            # Provenance, stated on the finding itself rather than inferred
            # later from which collector happens to be attached to it.
            "source": MANUAL_COLLECTOR,
            "source_label": MANUAL_SOURCE_LABEL,
            "evidence_class": EVIDENCE_INVESTIGATOR_IMPORTED,
            "import_method": "investigator_imported_search_result",
            "subject_name": str(target.attributes.get("display_name", target.normalized_value)),
            "subject_value": target.normalized_value,
            "candidate_key": url,
        }
        if profile:
            data.update(
                {
                    "platform": profile.platform,
                    "platform_label": profile.display_name,
                    "url_kind": profile.kind,
                    "handle": profile.handle,
                    "server_fetchable": profile.server_fetchable,
                    "fetch_note": profile.fetch_note,
                }
            )
        if is_image:
            data.update(
                {
                    "image_url": image_url,
                    "thumbnail_url": thumbnail_url,
                    "source_page_url": url,
                    "caption": item.caption or item.snippet,
                    # Said explicitly on every image record, because it is the
                    # one thing a reader might otherwise assume.
                    "analysis": "none",
                    "biometric_matching": False,
                    "interpretation": (
                        "Context evidence only: this image appears on a page associated "
                        "with the query. The platform performs no facial recognition and "
                        "makes no claim that any person depicted is the subject."
                    ),
                }
            )

        kind = FindingKind.IMAGE_EVIDENCE if is_image else FindingKind.MANUAL_SEARCH_RESULT
        finding = Finding(
            case_id=case_id,
            target_id=target_id,
            kind=kind,
            title=item.title or url,
            summary=_summary(item, is_image),
            data=data,
            collector=MANUAL_COLLECTOR,
            source_url=url,
            # An imported result is one investigator's judgement that a page was
            # worth keeping. That is not corroboration, so it starts low.
            confidence=0.2,
            confidence_reasons=[
                f"Imported by the investigator from {item.engine} for the query {item.query!r}",
                "Selected by a human from public search results; the platform did not "
                "fetch or rank it",
                "A page naming the subject is not evidence that the page is about them",
            ],
            classification=Classification.PERSONAL,
            observed_at=imported_at,
            dedupe_key=f"manual:{target.normalized_value}:{url}",
        )

        existing = session.scalar(
            select(Finding).where(
                Finding.case_id == case_id,
                Finding.dedupe_key == finding.dedupe_key,
            )
        )
        if existing is not None:
            findings.append(existing)
            continue

        session.add(finding)
        session.flush()

        # The imported record itself is the artefact: hashing it gives the
        # import the same integrity guarantee a fetched payload has.
        store.store(
            session,
            case_id=case_id,
            collector=MANUAL_COLLECTOR,
            source_url=url,
            content={
                "imported": item.model_dump(mode="json"),
                "normalized_url": url,
                "imported_at": imported_at.isoformat(),
                "evidence_class": EVIDENCE_INVESTIGATOR_IMPORTED,
            },
            retrieved_at=imported_at,
            finding=finding,
        )
        findings.append(finding)

    session.flush()
    log.info(
        "recon.results_imported",
        case_id=str(case_id),
        target_id=str(target_id),
        imported=len(findings),
    )
    return findings


def _summary(item: Any, is_image: bool) -> str:
    if is_image:
        return (
            f"Public image result imported from {item.engine} for {item.query!r}. "
            f"Context evidence: no facial or biometric analysis is performed."
        )
    return item.snippet or f"Public result imported from {item.engine} for {item.query!r}."


def _validated_public_url(raw: str) -> str:
    """Normalise a supplied URL and refuse anything that is not safely public.

    Runs the platform's existing SSRF guard rather than a second, weaker check,
    so a URL an investigator pastes is held to exactly the same standard as one
    a collector discovered: http(s) only, no credentials, no loopback, no
    private or link-local range, no cloud metadata endpoint.
    """
    text = (raw or "").strip()
    if not text:
        raise ValidationError("A result URL is required")
    if "://" not in text:
        text = f"https://{text}"

    lowered = text.lower()
    if not lowered.startswith(("http://", "https://")):
        raise ValidationError(f"{raw!r} is not an http(s) URL")
    if "@" in text.split("://", 1)[1].split("/", 1)[0]:
        raise ValidationError("URLs carrying credentials are not accepted")

    # Drop the fragment: it never reaches a server and only adds noise to the
    # identity of a result.
    text = text.split("#", 1)[0]
    validate_url(text)
    return text
