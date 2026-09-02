"""Report generation: content, citations and the export privacy policy."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.models import Case, CollectorRun, Entity, Evidence, Finding, Relationship, Target
from app.models.enums import (
    Classification,
    EntityType,
    FindingKind,
    MatchStrength,
    RelationshipType,
    ReportFormat,
    RunStatus,
    TargetType,
)
from app.reporting import build_report, render_html, render_json, render_markdown, render_report


@pytest.fixture
def populated_case(db_session):
    """A case with the full chain: target → run → finding → evidence → entity → edge."""
    case = Case(name="Example Domain Investigation", description="Due-diligence review")
    db_session.add(case)
    db_session.flush()

    target = Target(
        case_id=case.id,
        type=TargetType.DOMAIN,
        raw_input="Example.COM",
        normalized_value="example.com",
    )
    db_session.add(target)
    db_session.flush()

    runs = [
        CollectorRun(
            case_id=case.id,
            target_id=target.id,
            collector="dns",
            collector_version="1.0.0",
            status=RunStatus.SUCCESS,
            duration_ms=12.0,
        ),
        CollectorRun(
            case_id=case.id,
            target_id=target.id,
            collector="ctlog",
            collector_version="1.0.0",
            status=RunStatus.FAILED,
            error_type="CollectorError",
            error_message="crt.sh returned HTTP 502",
        ),
        CollectorRun(
            case_id=case.id,
            target_id=target.id,
            collector="search",
            collector_version="1.0.0",
            status=RunStatus.SKIPPED,
            error_message="No search provider is configured",
        ),
    ]
    db_session.add_all(runs)
    db_session.flush()

    strong = Finding(
        case_id=case.id,
        target_id=target.id,
        run_id=runs[0].id,
        kind=FindingKind.DNS_RECORD,
        title="DNS A for example.com",
        summary="1 A record",
        data={"hostname": "example.com", "records": ["93.184.215.14"]},
        collector="dns",
        source_url="dns://example.com/A",
        confidence=0.95,
        confidence_reasons=["Resolved from public DNS"],
        classification=Classification.PUBLIC,
        dedupe_key="dns:example.com:A",
        observed_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    weak = Finding(
        case_id=case.id,
        target_id=target.id,
        kind=FindingKind.SEARCH_RESULT,
        title="A page mentioning example.com",
        data={"url": "https://news.test/story"},
        collector="search",
        confidence=0.4,
        confidence_reasons=["Returned at rank 4"],
        classification=Classification.PUBLIC,
        dedupe_key="search:1",
    )
    sensitive = Finding(
        case_id=case.id,
        target_id=target.id,
        kind=FindingKind.EXPOSURE_SUMMARY,
        title="Breach exposure",
        data={"breach_count": 2, "breaches": [{"name": "ExampleForum"}]},
        collector="email",
        confidence=0.85,
        confidence_reasons=["Reported by HIBP"],
        classification=Classification.SENSITIVE,
        dedupe_key="email:exposure:1",
    )
    db_session.add_all([strong, weak, sensitive])
    db_session.flush()

    evidence = Evidence(
        case_id=case.id,
        finding_id=strong.id,
        collector="dns",
        source_url="dns://example.com/A",
        retrieved_at=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
        sha256="a" * 64,
        content_type="application/json",
        size_bytes=42,
        excerpt='{"records": ["93.184.215.14"]}',
    )
    db_session.add(evidence)

    domain = Entity(
        case_id=case.id,
        type=EntityType.DOMAIN,
        display_name="example.com",
        canonical_value="example.com",
        confidence=0.95,
        confidence_reasons=["Resolved from public DNS"],
    )
    address = Entity(
        case_id=case.id,
        type=EntityType.IP_ADDRESS,
        display_name="93.184.215.14",
        canonical_value="93.184.215.14",
        confidence=0.95,
    )
    db_session.add_all([domain, address])
    db_session.flush()
    domain.sources = [strong]

    db_session.add(
        Relationship(
            case_id=case.id,
            source_entity_id=domain.id,
            target_entity_id=address.id,
            type=RelationshipType.RESOLVES_TO,
            confidence=0.95,
            confidence_reasons=["Resolved from public DNS"],
            strength=MatchStrength.LIKELY_MATCH,
            collector="dns",
        )
    )
    db_session.flush()

    from app.services.timeline import build_timeline

    build_timeline(db_session, case.id)
    db_session.commit()
    return case


def test_model_gathers_the_whole_case(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    assert model.case_name == "Example Domain Investigation"
    assert model.counts["targets"] == 1
    assert model.counts["findings"] == 3
    assert model.counts["entities"] == 2
    assert model.counts["relationships"] == 1
    assert model.counts["evidence"] == 1
    assert model.counts["timeline_events"] == 1


def test_findings_are_ordered_by_confidence(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    scores = [finding.confidence for finding in model.findings]
    assert scores == sorted(scores, reverse=True)


def test_every_finding_carries_its_evidence_or_says_it_has_none(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    dns_finding = next(f for f in model.findings if f.collector == "dns")
    assert dns_finding.evidence
    assert dns_finding.evidence[0].sha256 == "a" * 64
    assert dns_finding.evidence[0].short_hash == "a" * 12


def test_confidence_bands_are_counted(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    bands = model.confidence["finding_bands"]
    assert bands["LIKELY_MATCH"] == 1
    assert bands["PROBABLE_MATCH"] == 1
    assert bands["WEAK_ASSOCIATION"] == 1
    assert model.confidence["relationship_bands"]["LIKELY_MATCH"] == 1


def test_executive_summary_reports_failures_and_skips(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    text = " ".join(model.executive_summary)
    assert "1 collector run(s) failed" in text
    assert "skipped" in text
    assert "partial" in text


def test_sources_show_what_ran_and_what_did_not(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    by_name = {source.collector: source for source in model.sources}
    assert by_name["dns"].successes == 1
    assert by_name["ctlog"].failures == 1
    assert by_name["search"].skipped == 1
    assert by_name["dns"].attribution


def test_min_confidence_filters_findings(db_session, populated_case):
    model = build_report(db_session, populated_case.id, min_confidence=0.5)
    assert model.counts["findings"] == 2
    assert all(finding.confidence >= 0.5 for finding in model.findings)


def test_export_policy_withholds_sensitive_content(db_session, populated_case):
    model = build_report(db_session, populated_case.id, max_classification=Classification.PERSONAL)
    sensitive = next(f for f in model.findings if f.classification == "SENSITIVE")
    assert sensitive.withheld is True
    assert sensitive.data["withheld"] is True
    assert "ExampleForum" not in json.dumps(sensitive.data)
    assert model.policy["withheld_findings"] == 1


def test_permissive_export_policy_keeps_sensitive_content(db_session, populated_case):
    model = build_report(
        db_session, populated_case.id, max_classification=Classification.RESTRICTED
    )
    sensitive = next(f for f in model.findings if f.classification == "SENSITIVE")
    assert sensitive.withheld is False
    assert sensitive.data["breach_count"] == 2


def test_methodology_and_limitations_are_always_present(db_session, populated_case):
    model = build_report(db_session, populated_case.id)
    assert len(model.methodology) >= 4
    assert any("shared username" in line.lower() for line in model.limitations)
    assert any("absence of evidence" in line.lower() for line in model.limitations)


def test_html_report_contains_every_section(db_session, populated_case):
    html = render_html(build_report(db_session, populated_case.id))
    for section in (
        "Case summary",
        "Executive summary",
        "Targets",
        "Key findings",
        "Entities",
        "Relationships",
        "Confidence assessment",
        "Timeline",
        "Evidence",
        "Sources",
        "Methodology",
        "Limitations",
    ):
        assert section in html, f"missing section: {section}"
    assert html.startswith("<!doctype html>")
    assert "sha256:" + "a" * 12 in html


def test_html_report_escapes_collected_content(db_session, populated_case):
    """Report content includes text from third-party sites; it must not execute."""
    hostile = Finding(
        case_id=populated_case.id,
        kind=FindingKind.HTTP_METADATA,
        title="<script>alert('xss')</script>",
        summary="<img src=x onerror=alert(1)>",
        data={"title": "<script>alert('data')</script>"},
        collector="http_meta",
        confidence=0.9,
        dedupe_key="hostile",
    )
    db_session.add(hostile)
    db_session.commit()

    html = render_html(build_report(db_session, populated_case.id))
    # The markup must appear only in escaped form: no live tag reaches the page.
    assert "<script>alert('xss')</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_markdown_report_contains_every_section(db_session, populated_case):
    markdown = render_markdown(build_report(db_session, populated_case.id))
    for heading in (
        "# Example Domain Investigation",
        "## Case summary",
        "## Executive summary",
        "## Targets",
        "## Key findings",
        "## Entities",
        "## Relationships",
        "## Confidence assessment",
        "## Timeline",
        "## Evidence",
        "## Sources",
        "## Methodology",
        "## Limitations",
    ):
        assert heading in markdown, f"missing heading: {heading}"
    assert "`sha256:" + "a" * 12 + "`" in markdown


def test_json_report_is_complete_and_machine_readable(db_session, populated_case):
    payload = json.loads(render_json(build_report(db_session, populated_case.id)))
    assert payload["case"]["name"] == "Example Domain Investigation"
    assert len(payload["findings"]) == 3
    assert payload["findings"][0]["evidence"][0]["sha256"] == "a" * 64
    assert payload["confidence"]["bands"]
    assert payload["methodology"] and payload["limitations"]
    assert payload["counts"]["entities"] == 2


def test_json_report_includes_all_findings_not_just_key_ones(db_session, populated_case):
    payload = json.loads(render_json(build_report(db_session, populated_case.id)))
    assert len(payload["findings"]) >= len(payload["key_findings"])


@pytest.mark.parametrize("report_format", list(ReportFormat))
def test_render_report_supports_every_format(db_session, populated_case, report_format):
    body = render_report(db_session, populated_case.id, report_format=report_format)
    assert body
    assert "Example Domain Investigation" in body


def test_unknown_case_raises(db_session):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        build_report(db_session, uuid.uuid4())


def test_empty_case_still_renders(db_session):
    empty = Case(name="Nothing found")
    db_session.add(empty)
    db_session.commit()

    model = build_report(db_session, empty.id)
    assert any("No findings" in line for line in model.executive_summary)
    html = render_html(model)
    assert "No findings met the report" in html
    markdown = render_markdown(model)
    assert "No targets were added" in markdown


async def test_report_endpoint_serves_each_format(api_client, db_session):
    """The API renders reports for a case created through the API itself."""
    created = await api_client.post("/api/v1/cases", json={"name": "API report case"})
    case_id = created.json()["id"]
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})

    html = await api_client.get(f"/api/v1/cases/{case_id}/report")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert "API report case" in html.text

    markdown = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.text.startswith("# API report case")

    payload = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    assert payload.json()["case"]["name"] == "API report case"


async def test_report_endpoint_rejects_an_unknown_format(api_client, case_id):
    response = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "pdf"})
    assert response.status_code == 422
