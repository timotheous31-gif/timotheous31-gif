#!/usr/bin/env python3
"""Load a demonstration case.

Everything here is fictional or reserved for documentation use: `example.com`,
`example.org` (RFC 2606), `203.0.113.0/24` (RFC 5737), and invented handles. No
real person or organisation is used as sample data, and the script never
contacts the network — the findings are fixtures, so the dashboard has
something to show before any collector runs.

    python scripts/seed.py [--database-url URL] [--reset]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Works from the repository (scripts/ beside backend/) and from the container
# image (scripts/ inside the installed application root).
for candidate in (
    Path(__file__).resolve().parents[1] / "backend",
    Path(__file__).resolve().parents[1],
):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.core.db import configure_engine, session_scope  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.enums import (  # noqa: E402
    Classification,
    FindingKind,
    RunStatus,
    TargetType,
)
from app.schemas.case import CaseCreate, TargetCreate  # noqa: E402
from app.services import cases as case_service  # noqa: E402

CASE_NAME = "Example Domain Investigation (demonstration)"

TARGETS = ["example.com", "example.org", "@exampleuser", "octocat/Hello-World"]

#: (kind, title, summary, data, collector, confidence, reasons, observed_at)
FINDINGS = [
    (
        FindingKind.DNS_RECORD,
        "DNS A for example.com",
        "1 A record for example.com",
        {
            "hostname": "example.com",
            "record_type": "A",
            "records": ["203.0.113.10"],
            "ttl": 300,
        },
        "dns",
        0.95,
        ["Resolved directly from public DNS, which is authoritative for this fact"],
        None,
    ),
    (
        FindingKind.DNS_RECORD,
        "DNS MX for example.com",
        "1 MX record for example.com",
        {
            "hostname": "example.com",
            "record_type": "MX",
            "records": ["10 mail.example.com."],
            "mail_hosts": ["mail.example.com"],
            "null_mx": False,
        },
        "dns",
        0.95,
        ["Resolved directly from public DNS, which is authoritative for this fact"],
        None,
    ),
    (
        FindingKind.DOMAIN_REGISTRATION,
        "RDAP registration for example.com",
        "Registration for example.com — Example Registrar LLC, 1995-08-14",
        {
            "handle": "example.com",
            "registrar": "Example Registrar LLC",
            "registrant_organization": "Example Documentation Trust",
            "created_at": "1995-08-14T04:00:00Z",
            "expires_at": "2027-08-13T04:00:00Z",
            "statuses": ["client transfer prohibited"],
            "notable_statuses": ["client transfer prohibited"],
            "nameservers": ["ns1.example.net", "ns2.example.net"],
            "privacy_protected": False,
        },
        "rdap",
        0.9,
        ["Published by the authoritative registry over RDAP"],
        datetime(1995, 8, 14, tzinfo=UTC),
    ),
    (
        FindingKind.SUBDOMAIN,
        "api.example.com",
        "Subdomain of example.com named in a public certificate",
        {
            "hostname": "api.example.com",
            "parent_domain": "example.com",
            "wildcard": False,
            "discovery_method": "certificate_transparency",
        },
        "ctlog",
        0.8,
        [
            "Named in a certificate recorded in a public CT log",
            "CT entries prove issuance, not that the host is currently live",
        ],
        None,
    ),
    (
        FindingKind.CERTIFICATE,
        "Certificate for www.example.com",
        "Issued by Example CA, valid from 2024-02-01",
        {
            "common_name": "www.example.com",
            "san_entries": ["example.com", "www.example.com", "api.example.com"],
            "issuer": "Example CA",
            "not_before": "2024-02-01T09:00:00+00:00",
            "not_after": "2025-05-01T09:00:00+00:00",
            "crtsh_id": 900000001,
        },
        "ctlog",
        0.9,
        ["Recorded in a public Certificate Transparency log by the issuing CA"],
        datetime(2024, 2, 1, 9, tzinfo=UTC),
    ),
    (
        FindingKind.HTTP_METADATA,
        "Example Documentation Domain",
        "HTTP 200 from https://example.com/",
        {
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "status_code": 200,
            "title": "Example Documentation Domain",
            "description": "A reserved domain used for documentation examples.",
            "https": True,
            "server_headers": {"server": "ExampleServer"},
            "redirect_chain": [],
        },
        "http_meta",
        0.9,
        ["Observed directly from the site's own HTTP response"],
        None,
    ),
    (
        FindingKind.HTTP_METADATA,
        "example.com links to https://github.com/exampleuser",
        "The site publishes a link to this profile",
        {
            "from": "https://example.com/",
            "to": "https://github.com/exampleuser",
            "relation": "site_links_to_profile",
        },
        "http_meta",
        0.9,
        ["The website itself publishes a link to this profile"],
        None,
    ),
    (
        FindingKind.ARCHIVE_SNAPSHOT,
        "First archived snapshot of example.com",
        "Internet Archive first captured this target on 1997-01-26",
        {
            "timestamp": "19970126045828",
            "original_url": "http://example.com/",
            "archived_url": "https://web.archive.org/web/19970126045828/http://example.com/",
            "position": "first",
        },
        "wayback",
        0.85,
        [
            "Recorded by the Internet Archive with a capture timestamp",
            "Archive coverage starts when a crawler first reached the site, "
            "which may be later than the site's actual creation",
        ],
        datetime(1997, 1, 26, 4, 58, 28, tzinfo=UTC),
    ),
    (
        FindingKind.CODE_PROFILE,
        "GitHub profile exampleuser",
        "User account with 8 public repositories, created 2011-01-25",
        {
            "login": "exampleuser",
            "account_type": "User",
            "name": "Example User",
            "blog": "https://example.com",
            "public_repos": 8,
            "followers": 42,
            "created_at": "2011-01-25T18:44:36Z",
            "html_url": "https://github.com/exampleuser",
        },
        "github",
        0.95,
        ["Retrieved from GitHub's public API for this account"],
        datetime(2011, 1, 25, 18, 44, 36, tzinfo=UTC),
    ),
    (
        FindingKind.REPOSITORY,
        "exampleuser/Hello-World",
        "Python repository with 12 star(s)",
        {
            "full_name": "exampleuser/Hello-World",
            "owner": "exampleuser",
            "language": "Python",
            "topics": ["example", "documentation"],
            "homepage": "https://example.com",
            "stars": 12,
            "created_at": "2011-01-26T19:01:12Z",
            "html_url": "https://github.com/exampleuser/Hello-World",
            "license": "MIT",
        },
        "github",
        0.95,
        ["Listed as public by GitHub's own API"],
        datetime(2011, 1, 26, 19, 1, 12, tzinfo=UTC),
    ),
    (
        FindingKind.USERNAME_PRESENCE,
        "exampleuser exists on GitLab",
        "The handle 'exampleuser' is registered on GitLab. This shows the name is "
        "taken, not who holds it.",
        {
            "platform": "gitlab",
            "platform_name": "GitLab",
            "category": "code",
            "username": "exampleuser",
            "profile_url": "https://gitlab.com/exampleuser",
            "exists": True,
            "response_status": 200,
        },
        "username",
        0.55,
        [
            "GitLab serves a public profile for this handle",
            "A matching username is not evidence of a shared owner; corroborate "
            "with a self-published link before drawing any conclusion",
        ],
        None,
    ),
]

RUNS = [
    ("dns", RunStatus.SUCCESS, None, None),
    ("rdap", RunStatus.SUCCESS, None, None),
    ("http_meta", RunStatus.SUCCESS, None, None),
    ("ctlog", RunStatus.SUCCESS, None, None),
    ("wayback", RunStatus.SUCCESS, None, None),
    ("github", RunStatus.SUCCESS, None, None),
    ("username", RunStatus.PARTIAL, None, None),
    (
        "search",
        RunStatus.SKIPPED,
        "CollectorUnavailable",
        "No search provider is configured. Set SEARCH_PROVIDER to brave, bing or "
        "serper and supply the matching API key.",
    ),
]


def seed(database_url: str | None, reset: bool) -> str:
    """Create the demonstration case and return its id."""
    from app.correlation.extraction import extract
    from app.correlation.resolver import EntityResolver
    from app.models import CollectorRun, Evidence, Finding
    from app.services.evidence import EvidenceStore
    from app.services.timeline import build_timeline

    if database_url:
        configure_engine(database_url)
    engine = configure_engine(database_url) if database_url else None
    if engine is not None:
        Base.metadata.create_all(engine)

    with session_scope() as session:
        if reset:
            existing, _ = case_service.list_cases(session, query=CASE_NAME)
            for case in existing:
                session.delete(case)
            session.flush()

        case = case_service.create_case(
            session,
            CaseCreate(
                name=CASE_NAME,
                description=(
                    "A demonstration case built from fixture data. Every target is a "
                    "reserved documentation domain or an invented handle; no collector "
                    "runs and no network request is made."
                ),
                tags=["demo", "fixture-data"],
            ),
        )

        targets = {}
        for value in TARGETS:
            target = case_service.add_target(session, case.id, TargetCreate(value=value))
            targets[target.type] = target
        primary = targets[TargetType.DOMAIN]

        for collector, status, error_type, error_message in RUNS:
            session.add(
                CollectorRun(
                    case_id=case.id,
                    target_id=primary.id,
                    collector=collector,
                    collector_version="1.0.0",
                    status=status,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    duration_ms=42.0,
                    error_type=error_type,
                    error_message=error_message,
                    stats={"seeded": True},
                )
            )
        session.flush()

        store = EvidenceStore()
        for (
            kind,
            title,
            summary,
            data,
            collector,
            confidence,
            reasons,
            observed,
        ) in FINDINGS:
            finding = Finding(
                case_id=case.id,
                target_id=primary.id,
                kind=kind,
                title=title,
                summary=summary,
                data=data,
                collector=collector,
                source_url=data.get("html_url") or data.get("url") or data.get("archived_url"),
                confidence=confidence,
                confidence_reasons=reasons,
                classification=(
                    Classification.PERSONAL
                    if kind is FindingKind.CODE_PROFILE
                    else Classification.PUBLIC
                ),
                dedupe_key=f"seed:{kind}:{title}"[:128],
                observed_at=observed,
            )
            session.add(finding)
            session.flush()
            store.store(
                session,
                case_id=case.id,
                collector=collector,
                source_url=finding.source_url,
                content={"seeded_fixture": True, **data},
                finding=finding,
            )

        session.flush()
        findings = list(session.query(Finding).filter(Finding.case_id == case.id))
        EntityResolver().resolve(session, case.id, extract(findings))
        build_timeline(session, case.id)

        counts = {
            "findings": len(findings),
            "evidence": session.query(Evidence).filter(Evidence.case_id == case.id).count(),
        }
        case_id = str(case.id)

    print(f"Seeded case {case_id}")
    print(f"  targets:  {len(TARGETS)}")
    print(f"  findings: {counts['findings']}")
    print(f"  evidence: {counts['evidence']}")
    print(f"\nOpen http://localhost:3000/cases/{case_id}")
    return case_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete any existing demonstration case first.",
    )
    args = parser.parse_args()
    try:
        seed(args.database_url, args.reset)
    except Exception as exc:  # a CLI should fail with a message, not a traceback
        print(f"Seeding failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
