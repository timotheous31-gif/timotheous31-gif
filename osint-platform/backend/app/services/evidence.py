"""Evidence store.

Every claim a report makes has to be traceable to something an investigator can
re-check. This module stores that something:

* the raw payload a collector received, written to disk under its SHA-256 —
  content addressing gives deduplication and tamper-evidence in one step;
* a database row recording the collector, source URL, retrieval time, hash,
  size and a privacy-filtered excerpt.

The payload is filtered for credentials *before* it is written, so the evidence
directory can never become a credential store. Hashes are computed over the
payload as stored, so verification tells you whether the stored artefact still
matches its recorded hash.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.collection import Evidence, Finding
from app.privacy.filter import PrivacyFilter

log = get_logger(__name__)

#: Payloads above this size are stored as a truncated excerpt with a note; the
#: hash still covers exactly what was stored.
MAX_STORED_BYTES = 2 * 1024 * 1024


@dataclass(slots=True)
class StoredEvidence:
    """What :meth:`EvidenceStore.store` produced."""

    evidence: Evidence
    created: bool
    sha256: str


def canonical_bytes(content: Any) -> bytes:
    """Serialise ``content`` deterministically so its hash is reproducible."""
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


def sha256_of(content: Any) -> str:
    """SHA-256 of ``content`` in its canonical form."""
    return hashlib.sha256(canonical_bytes(content)).hexdigest()


class EvidenceStore:
    """Persists raw payloads and their provenance records."""

    def __init__(
        self, settings: Settings | None = None, privacy: PrivacyFilter | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.privacy = privacy or PrivacyFilter(self.settings)

    @property
    def root(self) -> Path:
        return Path(self.settings.evidence_dir)

    def path_for(self, case_id: uuid.UUID, digest: str) -> Path:
        """Content-addressed path: ``<case>/<aa>/<sha256>.json``."""
        return self.root / str(case_id) / digest[:2] / f"{digest}.json"

    def store(
        self,
        session: Session,
        *,
        case_id: uuid.UUID,
        collector: str,
        source_url: str | None,
        content: Any,
        content_type: str = "application/json",
        retrieved_at: datetime | None = None,
        finding: Finding | None = None,
    ) -> StoredEvidence:
        """Store one payload, deduplicating on its hash within the case."""
        cleaned, redacted = self._sanitize(content)
        payload = canonical_bytes(cleaned)
        truncated = False
        if len(payload) > MAX_STORED_BYTES:
            payload = payload[:MAX_STORED_BYTES]
            truncated = True
        digest = hashlib.sha256(payload).hexdigest()

        existing = session.scalar(
            select(Evidence).where(Evidence.case_id == case_id, Evidence.sha256 == digest)
        )
        if existing is not None:
            # The same artefact often supports several findings; link each one
            # rather than keeping only the first.
            if finding is not None and all(linked.id != finding.id for linked in existing.findings):
                existing.findings.append(finding)
                session.flush()
            return StoredEvidence(evidence=existing, created=False, sha256=digest)

        raw_ref: str | None = None
        if self.settings.evidence_store_raw:
            raw_ref = self._write(case_id, digest, payload)

        evidence = Evidence(
            case_id=case_id,
            collector=collector,
            source_url=source_url[:2048] if source_url else None,
            retrieved_at=retrieved_at or datetime.now(UTC),
            sha256=digest,
            content_type=content_type,
            size_bytes=len(payload),
            raw_ref=raw_ref,
            excerpt=self.privacy.excerpt(
                payload.decode("utf-8", errors="replace") + ("\n[truncated]" if truncated else "")
            ),
            redacted=redacted,
        )
        if finding is not None:
            evidence.findings.append(finding)
        session.add(evidence)
        session.flush()
        log.info(
            "evidence.stored",
            collector=collector,
            sha256=digest[:12],
            bytes=len(payload),
            redacted=redacted,
        )
        return StoredEvidence(evidence=evidence, created=True, sha256=digest)

    def _sanitize(self, content: Any) -> tuple[Any, bool]:
        """Strip credentials from a payload before it is ever written."""
        if isinstance(content, dict):
            outcome = self.privacy.filter_finding(content)
            return outcome.data, outcome.redacted
        if isinstance(content, list):
            outcome = self.privacy.filter_finding({"items": content})
            return outcome.data.get("items", content), outcome.redacted
        if isinstance(content, str):
            from app.privacy.secrets import redact_text

            cleaned, matches = redact_text(content)
            return cleaned, bool(matches)
        return content, False

    def _write(self, case_id: uuid.UUID, digest: str, payload: bytes) -> str:
        path = self.path_for(case_id, digest)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(payload)
        except OSError as exc:
            # Losing the raw copy degrades evidence, so it is reported; the
            # hash and the database record still stand on their own.
            log.error(
                "evidence.write_failed",
                path=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""
        return str(path.relative_to(self.root))

    def read_raw(self, case_id: uuid.UUID, digest: str) -> bytes | None:
        """Read back a stored payload, or ``None`` when it is not on disk."""
        path = self.path_for(case_id, digest)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            log.error("evidence.read_failed", path=str(path), error_type=type(exc).__name__)
            return None

    def verify(self, evidence: Evidence) -> bool:
        """Check that the stored artefact still hashes to its recorded value."""
        payload = self.read_raw(evidence.case_id, evidence.sha256)
        if payload is None:
            return False
        return hashlib.sha256(payload).hexdigest() == evidence.sha256

    def verify_case(self, session: Session, case_id: uuid.UUID) -> dict[str, Any]:
        """Verify every stored artefact in a case."""
        rows = list(session.scalars(select(Evidence).where(Evidence.case_id == case_id)))
        verified = 0
        missing = 0
        mismatched: list[str] = []
        for row in rows:
            payload = self.read_raw(case_id, row.sha256)
            if payload is None:
                missing += 1
                continue
            if hashlib.sha256(payload).hexdigest() == row.sha256:
                verified += 1
            else:
                mismatched.append(row.sha256)
        return {
            "total": len(rows),
            "verified": verified,
            "missing_raw": missing,
            "mismatched": mismatched,
            "intact": not mismatched,
        }
