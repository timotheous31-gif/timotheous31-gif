"""The append-only security record.

One writer, one table, and a hard rule about what may go in it: **an audit entry
never contains a credential.** Not a password, not a session token, not an
Authorization header, not an API key for any provider, and not a raw request
body. :func:`record` enforces that by filtering the metadata it is handed rather
than trusting its callers to remember — a caller who passes a whole request body
gets the keys that are safe and a dropped-field count, not a leak.

What it *is* for: answering the questions a pilot customer asks after something
goes wrong. Who signed in, and from what kind of client. Who was let into the
workspace, by whom, at what role. Who ran an investigation, who cancelled one,
who exported a report, who recorded a decision that changed a conclusion.

Three properties make it worth trusting:

**It is append-only.** There is no update path and no delete path — not in this
module, not in any API route. A reviewer can rely on an entry meaning what it
said when it was written.

**It survives its subject.** Deleting a user or a workspace nulls the foreign key
rather than cascading, so removing an account cannot erase the record of what it
did.

**It never fails the operation it is recording.** A write that raises is logged
and swallowed. An audit table that can take the API down with it would be a
reliability problem wearing a security badge — and the structured application log
still carries the request. The trade is stated here rather than discovered later.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, request_id_var
from app.models.auth import AuditLogEntry
from app.models.enums import AuditEvent

log = get_logger(__name__)

#: Separators, stripped before a key is matched against the forbidden list.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Metadata keys that must never be persisted, matched case-insensitively as a
#: substring. Deliberately broad: a false positive costs a dropped field in an
#: audit entry, a false negative costs a credential in a customer's database.
FORBIDDEN_KEY_PARTS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth_header",
        "cookie",
        "session",
        "credential",
        "bearer",
        "private_key",
        "hash",
        "csrf",
        # Second-factor material. "secret" and "hash" already cover `mfa_secret`
        # and `code_hash`; these name what they do not. Deliberately specific
        # rather than a bare "mfa": this list matches on substrings, so "mfa"
        # would also drop `mfa_left_enabled` and `mfa_required` — facts an
        # operator needs, carrying nothing secret.
        "totp",
        "otpauth",
        # The code itself. Note this also matches `recovery_codes_issued`-style
        # names by substring, which is why the counters recorded alongside these
        # events are named `codes_issued` / `codes_remaining` instead: erring
        # wide here is correct, so the caller adapts rather than the filter.
        "recovery_code",
        "recovery-code",
    }
)

#: Ceiling on one stored value. An audit entry is a record, not a copy of a
#: payload — and an unbounded value is a way to fill a customer's disk.
MAX_VALUE_LENGTH = 500
#: Ceiling on how many keys one entry may carry.
MAX_METADATA_KEYS = 20

#: Value types that can be stored as they are. Anything else is stringified, so a
#: model instance or a response object cannot be smuggled in whole.
_SAFE_TYPES = (str, int, float, bool, type(None))


def safe_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The subset of ``raw`` that is safe to persist.

    Drops anything credential-shaped by key, truncates long values, bounds the
    number of keys, and records how many fields were dropped — so an entry that
    lost something says so instead of looking complete.
    """
    if not raw:
        return {}
    kept: dict[str, Any] = {}
    dropped = 0
    for key, value in raw.items():
        name = str(key)
        # Separators are normalised before matching, because a header arrives as
        # "x-api-key" and a form field as "apiKey" — and a filter that only knew
        # "api_key" would have let the first one straight through. Found by
        # testing the filter with a real header name rather than a field name.
        lowered = _NON_ALNUM.sub("", name.lower())
        if any(part.replace("_", "") in lowered for part in FORBIDDEN_KEY_PARTS):
            dropped += 1
            continue
        if len(kept) >= MAX_METADATA_KEYS:
            dropped += 1
            continue
        if isinstance(value, _SAFE_TYPES):
            stored: Any = value
        elif isinstance(value, list | tuple):
            stored = [str(item)[:MAX_VALUE_LENGTH] for item in list(value)[:20]]
        else:
            stored = str(value)
        if isinstance(stored, str) and len(stored) > MAX_VALUE_LENGTH:
            stored = stored[:MAX_VALUE_LENGTH]
        kept[name] = stored
    if dropped:
        kept["_dropped_fields"] = dropped
    return kept


def record(
    session: Session,
    *,
    event: AuditEvent,
    actor_user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    object_type: str = "",
    object_id: str | uuid.UUID = "",
    metadata: dict[str, Any] | None = None,
) -> AuditLogEntry | None:
    """Append one entry. Returns it, or ``None`` if the write failed.

    The entry is flushed but not committed: it joins the caller's transaction, so
    "the case was deleted" and "the deletion was recorded" either both happen or
    neither does.
    """
    entry = AuditLogEntry(
        event_type=event,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        object_type=object_type[:40],
        object_id=str(object_id)[:64],
        request_id=(request_id_var.get() or "")[:128],
        metadata_=safe_metadata(metadata),
    )
    try:
        session.add(entry)
        session.flush()
    except Exception as exc:  # pragma: no cover - defensive
        # Never fail the operation being recorded. The application log keeps the
        # request, and this failure is itself logged rather than silently lost.
        log.error(
            "audit.write_failed",
            audit_event=str(event),
            error_type=type(exc).__name__,
        )
        return None
    log.info(
        "audit.recorded",
        # Not ``event=``: structlog reserves that key for the message itself, and
        # passing it as a field raises at the call site.
        audit_event=str(event),
        object_type=entry.object_type or None,
        workspace_id=str(workspace_id) if workspace_id else None,
    )
    return entry


def entries_for_workspace(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLogEntry]:
    """One workspace's entries, newest first.

    Scoped by workspace because the audit log is customer data like everything
    else: an admin of one workspace has no business reading another's.
    """
    # Sign-in and sign-out are not workspace events — a user may belong to
    # several — so they are recorded with no workspace. They are still what an
    # administrator most wants to see, so they are included here when the actor is
    # a member of *this* workspace. Without that, "who signed in" would be
    # answerable only from the database, which defeats the point of the endpoint.
    from app.models.auth import WorkspaceMembership

    members = select(WorkspaceMembership.user_id).where(
        WorkspaceMembership.workspace_id == workspace_id
    )
    stmt = (
        select(AuditLogEntry)
        .where(
            or_(
                AuditLogEntry.workspace_id == workspace_id,
                and_(
                    AuditLogEntry.workspace_id.is_(None),
                    AuditLogEntry.actor_user_id.in_(members),
                ),
            )
        )
        .order_by(AuditLogEntry.occurred_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))
