"""Portable column types.

The platform runs on PostgreSQL in production but the test-suite runs on
SQLite, so primary keys use a decorator that emits a native ``UUID`` column on
PostgreSQL and a ``CHAR(36)`` elsewhere.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CHAR, Dialect, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import JSON


class GUID(TypeDecorator[uuid.UUID]):
    """Platform-independent UUID type."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


#: ``JSONB`` on PostgreSQL, plain ``JSON`` elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")
