"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.core.db import get_db

DbSession = Annotated[Session, Depends(get_db)]


def parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    """Parse ``value`` as a UUID or raise a 422."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be a UUID",
        ) from exc


def case_id_param(case_id: Annotated[str, Path(description="Case UUID")]) -> uuid.UUID:
    return parse_uuid(case_id, "case_id")


CaseId = Annotated[uuid.UUID, Depends(case_id_param)]
