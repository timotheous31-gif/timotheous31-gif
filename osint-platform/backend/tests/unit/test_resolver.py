"""Entity resolution: conservative merging and bounded inference."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.correlation.extraction import extract
from app.correlation.resolver import EntityResolver
from app.graph import build_graph
from app.models import Case, Entity, Finding, Relationship
from app.models.enums import EntityType, FindingKind, RelationshipType


@pytest.fixture
def case(db_session):
    row = Case(name="Resolution test")
    db_session.add(row)
    db_session.flush()
    return row


def add_finding(session, case, kind, data, collector="test") -> Finding:
    finding = Finding(
        case_id=case.id,
        kind=kind,
        title="t",
        data=data,
        collector=collector,
        source_url="https://example.com/source",
        dedupe_key=str(uuid.uuid4()),
    )
    session.add(finding)
    session.flush()
    return finding


def resolve(session, case, findings):
    return EntityResolver().resolve(session, case.id, extract(findings))


def test_entities_and_relationships_are_persisted(db_session, case):
    finding = add_finding(
        db_session,
        case,
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    summary = resolve(db_session, case, [finding])

    assert summary.entities_created == 2
    assert summary.relationships_created == 1
    entities = db_session.scalars(select(Entity).where(Entity.case_id == case.id)).all()
    assert {e.canonical_value for e in entities} == {"example.com", "93.184.215.14"}
    edge = db_session.scalar(select(Relationship).where(Relationship.case_id == case.id))
    assert edge.type is RelationshipType.RESOLVES_TO
    assert edge.confidence >= 0.9
    assert edge.confidence_reasons


def test_entities_carry_their_source_findings(db_session, case):
    finding = add_finding(
        db_session,
        case,
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    resolve(db_session, case, [finding])
    domain = db_session.scalar(select(Entity).where(Entity.canonical_value == "example.com"))
    assert [source.id for source in domain.sources] == [finding.id]


def test_reresolution_updates_rather_than_duplicates(db_session, case):
    finding = add_finding(
        db_session,
        case,
        FindingKind.DNS_RECORD,
        {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
    )
    resolve(db_session, case, [finding])
    second = resolve(db_session, case, [finding])

    assert second.entities_created == 0
    assert second.entities_updated == 2
    assert second.relationships_created == 0
    assert db_session.scalars(select(Entity).where(Entity.case_id == case.id)).all().__len__() == 2


def test_same_username_across_platforms_is_inferred_not_merged(db_session, case):
    findings = [
        add_finding(
            db_session,
            case,
            FindingKind.USERNAME_PRESENCE,
            {
                "username": "exampleuser",
                "platform": platform,
                "profile_url": f"https://{platform}.test/exampleuser",
            },
        )
        for platform in ("github", "gitlab")
    ]
    resolve(db_session, case, findings)

    accounts = db_session.scalars(
        select(Entity).where(Entity.type == EntityType.SOCIAL_ACCOUNT)
    ).all()
    assert len(accounts) == 2, "accounts on different platforms must stay distinct"

    inferred = db_session.scalars(
        select(Relationship).where(Relationship.type == RelationshipType.SAME_USERNAME)
    ).all()
    cross_platform = [edge for edge in inferred if edge.attributes.get("inferred")]
    assert cross_platform
    assert all(edge.confidence <= 0.6 for edge in cross_platform)
    assert all(edge.type is not RelationshipType.POSSIBLY_SAME_ENTITY for edge in inferred)


def test_short_handles_do_not_produce_inferred_links(db_session, case):
    findings = [
        add_finding(
            db_session,
            case,
            FindingKind.USERNAME_PRESENCE,
            {"username": "abc", "platform": platform, "profile_url": "u"},
        )
        for platform in ("github", "gitlab")
    ]
    summary = resolve(db_session, case, findings)
    assert summary.inferred_links == 0


def test_two_accounts_linking_to_one_site_are_flagged_for_review(db_session, case):
    findings = [
        add_finding(
            db_session,
            case,
            FindingKind.CODE_PROFILE,
            {
                "login": "exampleuser",
                "url": "https://example.com",
                "relation": "profile_links_to_site",
            },
        ),
        add_finding(
            db_session,
            case,
            FindingKind.HTTP_METADATA,
            {
                "from": "https://example.com/",
                "to": "https://gitlab.com/exampleuser",
                "relation": "site_links_to_profile",
            },
        ),
    ]
    resolve(db_session, case, findings)

    possible = db_session.scalars(
        select(Relationship).where(Relationship.type == RelationshipType.POSSIBLY_SAME_ENTITY)
    ).all()
    assert possible
    assert all(edge.confidence_reasons for edge in possible)


def test_nothing_merges_entities_with_different_canonical_values(db_session, case):
    """The resolver may suggest a merge; it must never perform one."""
    findings = [
        add_finding(
            db_session,
            case,
            FindingKind.USERNAME_PRESENCE,
            {"username": "exampleuser", "platform": p, "profile_url": f"https://{p}/exampleuser"},
        )
        for p in ("github", "gitlab", "keybase")
    ]
    resolve(db_session, case, findings)
    accounts = db_session.scalars(
        select(Entity).where(Entity.type == EntityType.SOCIAL_ACCOUNT)
    ).all()
    assert len(accounts) == 3
    assert len({a.canonical_value for a in accounts}) == 3


def test_confidence_reasons_are_recorded_on_every_edge(db_session, case):
    finding = add_finding(
        db_session,
        case,
        FindingKind.DOMAIN_REGISTRATION,
        {
            "handle": "example.org",
            "registrar": "Example Registrar LLC",
            "registrant_organization": "Example Documentation Trust",
            "privacy_protected": False,
        },
    )
    resolve(db_session, case, [finding])
    for edge in db_session.scalars(select(Relationship)).all():
        assert edge.confidence_reasons, f"{edge.type} has no reasons"
        assert 0.0 <= edge.confidence <= 1.0
        assert edge.strength


def test_resolution_output_feeds_the_graph(db_session, case):
    findings = [
        add_finding(
            db_session,
            case,
            FindingKind.DNS_RECORD,
            {"hostname": "example.com", "record_type": "A", "records": ["93.184.215.14"]},
        ),
        add_finding(
            db_session,
            case,
            FindingKind.SUBDOMAIN,
            {"hostname": "api.example.com", "parent_domain": "example.com"},
        ),
    ]
    resolve(db_session, case, findings)

    entities = db_session.scalars(select(Entity).where(Entity.case_id == case.id)).all()
    relationships = db_session.scalars(
        select(Relationship).where(Relationship.case_id == case.id)
    ).all()
    graph = build_graph(entities, relationships)

    assert len(graph.nodes()) == len(entities)
    assert len(graph.edges()) == len(relationships)
    exported = graph.to_dict()
    assert exported["stats"]["node_count"] == len(entities)


def test_empty_extraction_is_a_no_op(db_session, case):
    summary = resolve(db_session, case, [])
    assert summary.as_dict() == {
        "entities_created": 0,
        "entities_updated": 0,
        "relationships_created": 0,
        "relationships_updated": 0,
        "inferred_links": 0,
        "merges_suggested": 0,
    }
