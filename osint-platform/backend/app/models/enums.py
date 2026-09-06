"""Enumerations shared by the ORM models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class TargetType(StrEnum):
    """A normalised investigation target."""

    USERNAME = "USERNAME"
    DOMAIN = "DOMAIN"
    EMAIL = "EMAIL"
    #: A named natural person. Never inferred from free text — a bare name is
    #: ambiguous between a person and an organisation, so the caller chooses.
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    URL = "URL"
    IP = "IP"
    REPOSITORY = "REPOSITORY"
    SOCIAL_PROFILE = "SOCIAL_PROFILE"


class CaseStatus(StrEnum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    ARCHIVED = "ARCHIVED"


class TargetStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    TIMEOUT = "TIMEOUT"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EntityType(StrEnum):
    PERSONA = "PERSONA"
    USERNAME = "USERNAME"
    EMAIL = "EMAIL"
    DOMAIN = "DOMAIN"
    ORGANIZATION = "ORGANIZATION"
    WEBSITE = "WEBSITE"
    REPOSITORY = "REPOSITORY"
    SOCIAL_ACCOUNT = "SOCIAL_ACCOUNT"
    IP_ADDRESS = "IP_ADDRESS"
    CERTIFICATE = "CERTIFICATE"
    DOCUMENT = "DOCUMENT"


class RelationshipType(StrEnum):
    USES_USERNAME = "USES_USERNAME"
    OWNS_DOMAIN = "OWNS_DOMAIN"
    LINKS_TO = "LINKS_TO"
    MENTIONS = "MENTIONS"
    MEMBER_OF = "MEMBER_OF"
    CONTRIBUTED_TO = "CONTRIBUTED_TO"
    HOSTED_ON = "HOSTED_ON"
    RESOLVES_TO = "RESOLVES_TO"
    ISSUED_FOR = "ISSUED_FOR"
    REFERENCED_BY = "REFERENCED_BY"
    SAME_USERNAME = "SAME_USERNAME"
    POSSIBLY_SAME_ENTITY = "POSSIBLY_SAME_ENTITY"
    SUBDOMAIN_OF = "SUBDOMAIN_OF"
    HAS_MX = "HAS_MX"
    ARCHIVED_AS = "ARCHIVED_AS"


class MatchStrength(StrEnum):
    """Bucketed interpretation of a correlation confidence score."""

    LIKELY_MATCH = "LIKELY_MATCH"
    PROBABLE_MATCH = "PROBABLE_MATCH"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    WEAK_ASSOCIATION = "WEAK_ASSOCIATION"


class Classification(StrEnum):
    """Privacy classification assigned to every finding."""

    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    RESTRICTED = "RESTRICTED"


class FindingKind(StrEnum):
    """Machine-readable category of a normalised finding."""

    DNS_RECORD = "DNS_RECORD"
    DOMAIN_REGISTRATION = "DOMAIN_REGISTRATION"
    HTTP_METADATA = "HTTP_METADATA"
    SECURITY_HEADER = "SECURITY_HEADER"
    CERTIFICATE = "CERTIFICATE"
    SUBDOMAIN = "SUBDOMAIN"
    ARCHIVE_SNAPSHOT = "ARCHIVE_SNAPSHOT"
    REPOSITORY = "REPOSITORY"
    CODE_PROFILE = "CODE_PROFILE"
    COMMIT_ACTIVITY = "COMMIT_ACTIVITY"
    ORGANIZATION_MEMBERSHIP = "ORGANIZATION_MEMBERSHIP"
    USERNAME_PRESENCE = "USERNAME_PRESENCE"
    SEARCH_RESULT = "SEARCH_RESULT"
    #: A public page that mentions a PERSON target's name. A candidate for
    #: being about that person, never an assertion that it is.
    PERSON_CANDIDATE = "PERSON_CANDIDATE"
    #: A public search result the investigator reviewed and imported by hand.
    #: Distinct from SEARCH_RESULT, which a search API returned to the platform:
    #: conflating them would misstate how the evidence was obtained.
    MANUAL_SEARCH_RESULT = "MANUAL_SEARCH_RESULT"
    #: A publicly indexed image and the page it appears on. Context evidence,
    #: never biometric identification.
    IMAGE_EVIDENCE = "IMAGE_EVIDENCE"
    EMAIL_DOMAIN = "EMAIL_DOMAIN"
    EXPOSURE_SUMMARY = "EXPOSURE_SUMMARY"
    POTENTIAL_SECRET_EXPOSURE = "POTENTIAL_SECRET_EXPOSURE"  # noqa: S105 - a category name
    IP_REVERSE = "IP_REVERSE"
    NOTE = "NOTE"


class ReportFormat(StrEnum):
    HTML = "html"
    MARKDOWN = "md"
    JSON = "json"
