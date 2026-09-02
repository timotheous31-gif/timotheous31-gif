"""Entity and relationship extraction from findings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.correlation.extraction import extract
from app.models.enums import EntityType, FindingKind, RelationshipType


@dataclass
class FakeFinding:
    kind: FindingKind
    data: dict[str, Any]
    collector: str = "test"
    source_url: str | None = "https://example.com/source"
    confidence: float = 0.9
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def entity(result, entity_type, value):
    return result.entities[(entity_type, value)]


def has_edge(result, source, target, rel_type):
    return (source, target, rel_type) in result.relationships


def test_a_records_create_domain_and_ip_entities():
    result = extract(
        [
            FakeFinding(
                FindingKind.DNS_RECORD,
                {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
            )
        ]
    )
    assert entity(result, EntityType.DOMAIN, "example.com")
    assert entity(result, EntityType.IP_ADDRESS, "93.184.215.14")
    assert has_edge(
        result,
        (EntityType.DOMAIN, "example.com"),
        (EntityType.IP_ADDRESS, "93.184.215.14"),
        RelationshipType.RESOLVES_TO,
    )


def test_mx_records_create_mail_host_edges():
    result = extract(
        [
            FakeFinding(
                FindingKind.DNS_RECORD,
                {
                    "hostname": "example.com",
                    "record_type": "MX",
                    "records": ["10 mail.example.net."],
                    "mail_hosts": ["mail.example.net"],
                },
            )
        ]
    )
    assert has_edge(
        result,
        (EntityType.DOMAIN, "example.com"),
        (EntityType.DOMAIN, "mail.example.net"),
        RelationshipType.HAS_MX,
    )


def test_nameservers_are_shared_infrastructure_not_ownership():
    result = extract(
        [
            FakeFinding(
                FindingKind.DNS_RECORD,
                {
                    "hostname": "example.com",
                    "record_type": "NS",
                    "records": ["a.iana-servers.net."],
                    "nameservers": ["a.iana-servers.net"],
                },
            )
        ]
    )
    edge = result.relationships[
        (
            (EntityType.DOMAIN, "example.com"),
            (EntityType.DOMAIN, "a.iana-servers.net"),
            RelationshipType.HOSTED_ON,
        )
    ]
    assert edge.signals[0].key == "shared_infrastructure"
    assert edge.signals[0].ceiling <= 0.7


def test_registration_creates_organisation_ownership_when_public():
    result = extract(
        [
            FakeFinding(
                FindingKind.DOMAIN_REGISTRATION,
                {
                    "handle": "EXAMPLE.ORG",
                    "registrar": "Example Registrar LLC",
                    "registrant_organization": "Example Documentation Trust",
                    "created_at": "1995-08-31T04:00:00Z",
                    "privacy_protected": False,
                },
            )
        ]
    )
    assert has_edge(
        result,
        (EntityType.ORGANIZATION, "example documentation trust"),
        (EntityType.DOMAIN, "example.org"),
        RelationshipType.OWNS_DOMAIN,
    )


def test_redacted_registration_creates_no_organisation():
    result = extract(
        [
            FakeFinding(
                FindingKind.DOMAIN_REGISTRATION,
                {"handle": "example.com", "registrar": "R", "privacy_protected": True},
            )
        ]
    )
    assert not any(key[0] is EntityType.ORGANIZATION for key in result.entities)


def test_site_linking_to_profile_is_a_strong_edge():
    result = extract(
        [
            FakeFinding(
                FindingKind.HTTP_METADATA,
                {
                    "from": "https://example.com/",
                    "to": "https://github.com/exampleorg",
                    "relation": "site_links_to_profile",
                },
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.type is RelationshipType.LINKS_TO
    assert edge.signals[0].key == "site_links_profile"
    assert edge.signals[0].score >= 0.9


def test_subdomain_findings_create_hierarchy():
    result = extract(
        [
            FakeFinding(
                FindingKind.SUBDOMAIN,
                {"hostname": "api.example.com", "parent_domain": "example.com"},
            )
        ]
    )
    assert has_edge(
        result,
        (EntityType.DOMAIN, "api.example.com"),
        (EntityType.DOMAIN, "example.com"),
        RelationshipType.SUBDOMAIN_OF,
    )


def test_certificates_are_issued_for_each_san():
    result = extract(
        [
            FakeFinding(
                FindingKind.CERTIFICATE,
                {
                    "common_name": "www.example.com",
                    "crtsh_id": 900000001,
                    "san_entries": ["www.example.com", "example.com"],
                    "issuer": "Let's Encrypt",
                },
            )
        ]
    )
    certificate = (EntityType.CERTIFICATE, "cert:900000001")
    assert has_edge(
        result, certificate, (EntityType.DOMAIN, "www.example.com"), RelationshipType.ISSUED_FOR
    )
    assert has_edge(
        result, certificate, (EntityType.DOMAIN, "example.com"), RelationshipType.ISSUED_FOR
    )


def test_username_presence_never_asserts_identity():
    result = extract(
        [
            FakeFinding(
                FindingKind.USERNAME_PRESENCE,
                {
                    "username": "exampleuser",
                    "platform": "github",
                    "profile_url": "https://github.com/exampleuser",
                },
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.type is RelationshipType.SAME_USERNAME
    assert edge.type is not RelationshipType.POSSIBLY_SAME_ENTITY
    assert edge.signals[0].ceiling <= 0.6


def test_short_usernames_use_the_weaker_rule():
    result = extract(
        [
            FakeFinding(
                FindingKind.USERNAME_PRESENCE,
                {"username": "abc", "platform": "github", "profile_url": "u"},
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.signals[0].key == "same_common_username"


def test_repository_links_owner_and_homepage():
    result = extract(
        [
            FakeFinding(
                FindingKind.REPOSITORY,
                {
                    "full_name": "exampleuser/Hello-World",
                    "owner": "exampleuser",
                    "homepage": "https://example.com",
                    "language": "Python",
                },
            )
        ]
    )
    account = (EntityType.SOCIAL_ACCOUNT, "github:exampleuser")
    repository = (EntityType.REPOSITORY, "github:exampleuser/hello-world")
    assert has_edge(result, account, repository, RelationshipType.CONTRIBUTED_TO)
    assert any(edge.type is RelationshipType.LINKS_TO for edge in result.relationships.values())


def test_membership_is_self_declared():
    result = extract(
        [
            FakeFinding(
                FindingKind.ORGANIZATION_MEMBERSHIP,
                {"login": "exampleuser", "organization": "exampleorg"},
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.type is RelationshipType.MEMBER_OF
    assert edge.signals[0].key == "self_declared_membership"


def test_commit_activity_attributes_contributions():
    result = extract(
        [
            FakeFinding(
                FindingKind.COMMIT_ACTIVITY,
                {"repository": "exampleuser/Hello-World", "authors": {"exampleuser": 2}},
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.type is RelationshipType.CONTRIBUTED_TO
    assert edge.attributes["commits"] == 2


def test_profile_link_to_site_is_self_published():
    result = extract(
        [
            FakeFinding(
                FindingKind.CODE_PROFILE,
                {
                    "login": "exampleuser",
                    "url": "https://example.com",
                    "relation": "profile_links_to_site",
                },
            )
        ]
    )
    edge = next(iter(result.relationships.values()))
    assert edge.signals[0].key == "profile_links_site"


def test_email_links_to_its_domain():
    result = extract(
        [
            FakeFinding(
                FindingKind.EMAIL_DOMAIN, {"address": "user@example.com", "domain": "example.com"}
            )
        ]
    )
    assert entity(result, EntityType.EMAIL, "user@example.com")
    assert entity(result, EntityType.DOMAIN, "example.com")


def test_archive_snapshot_creates_a_document():
    result = extract(
        [
            FakeFinding(
                FindingKind.ARCHIVE_SNAPSHOT,
                {
                    "original_url": "http://example.com/",
                    "archived_url": "https://web.archive.org/web/1997/http://example.com/",
                    "timestamp": "19970126045828",
                },
            )
        ]
    )
    assert any(key[0] is EntityType.DOCUMENT for key in result.entities)


def test_duplicate_findings_merge_rather_than_duplicate():
    finding = FakeFinding(
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    other = FakeFinding(
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    result = extract([finding, other])
    domain = entity(result, EntityType.DOMAIN, "example.com")
    assert len(result.entities) == 2
    assert len(domain.source_finding_ids) == 2


def test_self_edges_are_dropped():
    result = extract(
        [
            FakeFinding(
                FindingKind.SUBDOMAIN, {"hostname": "example.com", "parent_domain": "example.com"}
            )
        ]
    )
    assert result.relationships == {}


def test_unknown_finding_kinds_are_ignored():
    assert extract([FakeFinding(FindingKind.NOTE, {"x": 1})]).entities == {}


def test_malformed_finding_does_not_stop_extraction():
    good = FakeFinding(
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    bad = FakeFinding(FindingKind.CERTIFICATE, {"common_name": "x", "san_entries": None})
    result = extract([bad, good])
    assert entity(result, EntityType.DOMAIN, "example.com")


@pytest.mark.parametrize("value", [{}, {"hostname": ""}, {"records": []}])
def test_empty_dns_payloads_are_safe(value):
    assert extract([FakeFinding(FindingKind.DNS_RECORD, value)]).entities == {}
