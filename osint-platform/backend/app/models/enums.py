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


class AnalystDecision(StrEnum):
    """An analyst's judgement about an association.

    Deliberately separate from automated confidence: a decision records what a
    human concluded, and never edits what the platform computed. The two are
    stored apart so a report can show both.
    """

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class DecisionSubject(StrEnum):
    """What an analyst decision is about."""

    CANDIDATE = "CANDIDATE"
    SOCIAL_PROFILE = "SOCIAL_PROFILE"
    IMAGE = "IMAGE"
    CONTACT = "CONTACT"


class ContactType(StrEnum):
    """The kind of public contact point. Professional and business only."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    WEBSITE = "WEBSITE"
    CONTACT_PAGE = "CONTACT_PAGE"


class ContactClassification(StrEnum):
    """How well established a public contact point is.

    A classification is about *provenance*, not about how useful the value
    looks. A professional address a person published on their own profile is
    self-published however plausible it seems; an address found in a passing
    mention stays unverified however official the domain looks.
    """

    #: Published by the organisation itself on an official page.
    VERIFIED_PUBLIC_BUSINESS = "VERIFIED_PUBLIC_BUSINESS"
    #: Published in a professional registry or directory (ORCID, a staff page).
    PUBLIC_PROFESSIONAL = "PUBLIC_PROFESSIONAL"
    #: The person published it themselves on their own public profile.
    PUBLIC_SELF_PUBLISHED = "PUBLIC_SELF_PUBLISHED"
    #: Seen on a public page, with nothing establishing who it belongs to.
    UNVERIFIED_PUBLIC_REFERENCE = "UNVERIFIED_PUBLIC_REFERENCE"


class ProfileAccess(StrEnum):
    """How reachable a public profile page is, as observed — never as assumed."""

    PUBLIC = "PUBLIC"
    #: The platform refuses anonymous server-side access. Recorded, not evaded.
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


class ImageFetchState(StrEnum):
    """Whether image bytes were actually retrieved.

    The distinction matters for evidence: only FETCHED carries a SHA-256 of
    bytes this platform read. REFERENCE_ONLY means the URL is recorded and
    nothing was downloaded, which is the honest outcome when a platform blocks
    anonymous fetches.
    """

    FETCHED = "FETCHED"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    BLOCKED = "BLOCKED"


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
