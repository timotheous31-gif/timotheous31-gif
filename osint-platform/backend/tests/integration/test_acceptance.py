"""The documented end-to-end workflow, exercised as one test.

This mirrors the acceptance criteria in the README: create a case, add a
target, run the investigation, and confirm that findings, entities,
relationships, confidence scores, the privacy filter, the graph, the timeline,
the evidence chain and the report all appear and agree with one another.

Collectors are stubbed so the test is hermetic, but everything downstream of
them — normalisation, filtering, persistence, correlation, scoring, evidence,
reporting — is the real implementation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.collectors.base import BaseCollector, CollectorResult, FindingDraft, RawPayload
from app.collectors.registry import _REGISTRY
from app.models.enums import Classification, FindingKind, TargetType


class AcceptanceDNS(BaseCollector):
    name = "acc_dns"
    version = "1.0.0"
    source_attribution = "Public DNS (system resolvers)"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult(stats={"queries": 2})
        payload = RawPayload(
            source_url=f"dns://{target.value}/A",
            content={"hostname": target.value, "records": ["203.0.113.10"]},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.DNS_RECORD,
                title=f"DNS A for {target.value}",
                summary=f"1 A record for {target.value}",
                data={
                    "hostname": target.value,
                    "record_type": "A",
                    "records": ["203.0.113.10"],
                },
                confidence=0.95,
                confidence_reasons=["Resolved directly from public DNS"],
                dedupe_key=f"dns:{target.value}:A",
            ),
            payload,
        )
        return result


class AcceptanceRDAP(BaseCollector):
    name = "acc_rdap"
    version = "1.0.0"
    source_attribution = "RDAP (IANA bootstrap)"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult()
        payload = RawPayload(
            source_url=f"https://rdap.org/domain/{target.value}",
            content={"ldhName": target.value, "registrar": "Example Registrar LLC"},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.DOMAIN_REGISTRATION,
                title=f"RDAP registration for {target.value}",
                summary="Registered 1995-08-14 through Example Registrar LLC",
                data={
                    "handle": target.value,
                    "registrar": "Example Registrar LLC",
                    "registrant_organization": "Example Documentation Trust",
                    "created_at": "1995-08-14T04:00:00Z",
                    "privacy_protected": False,
                },
                confidence=0.9,
                confidence_reasons=["Published by the authoritative registry over RDAP"],
                observed_at=datetime(1995, 8, 14, tzinfo=UTC),
                dedupe_key=f"rdap:{target.value}",
            ),
            payload,
        )
        return result


class AcceptanceHTTP(BaseCollector):
    """Returns something credential-shaped, to prove the filter runs in-line."""

    name = "acc_http"
    version = "1.0.0"
    source_attribution = "Direct HTTP request to the public website"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult()
        payload = RawPayload(
            source_url=f"https://{target.value}/",
            content={"html": "<title>Example</title> deploy key AKIA1234567890ABCDEF"},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.HTTP_METADATA,
                title="Example Documentation Domain",
                summary=f"HTTP 200 from https://{target.value}/",
                data={
                    "url": f"https://{target.value}/",
                    "final_url": f"https://{target.value}/",
                    "status_code": 200,
                    "title": "Example Documentation Domain",
                    "https": True,
                    "deploy_note": "configured with AKIA1234567890ABCDEF",
                },
                confidence=0.9,
                confidence_reasons=["Observed directly from the site's own HTTP response"],
                dedupe_key=f"http:{target.value}:metadata",
            ),
            payload,
        )
        result.add(
            FindingDraft(
                kind=FindingKind.HTTP_METADATA,
                title=f"{target.value} links to https://github.com/exampleuser",
                summary="The site publishes a link to this profile",
                data={
                    "from": f"https://{target.value}/",
                    "to": "https://github.com/exampleuser",
                    "relation": "site_links_to_profile",
                },
                confidence=0.9,
                confidence_reasons=["The website itself publishes a link to this profile"],
                dedupe_key=f"http:{target.value}:link",
            ),
            payload,
        )
        return result


class AcceptanceUnavailable(BaseCollector):
    name = "acc_search"
    version = "1.0.0"
    supported_targets = [TargetType.DOMAIN]
    requires_api_key = True

    def is_available(self):
        return False, "No search provider is configured. Set SEARCH_PROVIDER."

    async def collect(self, target, ctx):  # pragma: no cover - never reached
        return CollectorResult()


class AcceptanceFailing(BaseCollector):
    name = "acc_ctlog"
    version = "1.0.0"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        from app.core.errors import CollectorError

        raise CollectorError("crt.sh returned HTTP 502")


@pytest.fixture(autouse=True)
def acceptance_environment(monkeypatch, tmp_path):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)
    reset_settings_cache()

    original = dict(_REGISTRY)
    _REGISTRY.clear()
    for cls in (
        AcceptanceDNS,
        AcceptanceRDAP,
        AcceptanceHTTP,
        AcceptanceUnavailable,
        AcceptanceFailing,
    ):
        _REGISTRY[cls.name] = cls
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)
    reset_settings_cache()


async def test_the_documented_workflow_end_to_end(api_client):
    # 1. Create a case.
    created = await api_client.post(
        "/api/v1/cases",
        json={"name": "Example Domain Investigation", "tags": ["acceptance"]},
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert created.json()["status"] == "NEW"

    # 2. Add a target; its type is inferred and its value normalised.
    target = await api_client.post(
        f"/api/v1/cases/{case_id}/targets", json={"value": "  Example.COM. "}
    )
    assert target.status_code == 201
    assert target.json()["type"] == "DOMAIN"
    assert target.json()["normalized_value"] == "example.com"

    # 3. Run the investigation.
    run = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert run.status_code == 202
    job_id = run.json()["job"]["id"]

    job = (await api_client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["state"] == "COMPLETE"
    assert job["progress"] == 1.0

    # 4. Every collector ran, and the failures are visible rather than hidden.
    runs = (await api_client.get(f"/api/v1/cases/{case_id}/runs")).json()
    by_collector = {item["collector"]: item for item in runs}
    assert by_collector["acc_dns"]["status"] == "SUCCESS"
    assert by_collector["acc_ctlog"]["status"] == "FAILED"
    assert "502" in by_collector["acc_ctlog"]["error_message"]
    assert by_collector["acc_search"]["status"] == "SKIPPED"
    assert "SEARCH_PROVIDER" in by_collector["acc_search"]["error_message"]

    # 5. Findings exist, are scored, and every score has its reasons.
    findings = (await api_client.get(f"/api/v1/cases/{case_id}/findings")).json()
    assert findings["total"] == 4
    assert all(item["confidence_reasons"] for item in findings["items"])
    assert all(0.0 <= item["confidence"] <= 1.0 for item in findings["items"])

    # 6. The privacy filter ran before persistence: the credential is gone.
    serialized = json.dumps(findings)
    assert "AKIA1234567890ABCDEF" not in serialized
    redacted = [item for item in findings["items"] if item["redacted"]]
    assert redacted, "the credential-bearing finding should be marked redacted"
    assert redacted[0]["classification"] == Classification.RESTRICTED.value

    # 7. Every finding traces to a hash-verified artefact.
    for item in findings["items"]:
        assert item["evidence"], f"{item['title']} has no evidence"
        assert len(item["evidence"][0]["sha256"]) == 64

    verification = (await api_client.get(f"/api/v1/cases/{case_id}/evidence/verify")).json()
    assert verification["intact"] is True
    assert verification["verified"] == verification["total"] > 0

    # 8. Entities and relationships were derived, with reasons.
    entities = (await api_client.get(f"/api/v1/cases/{case_id}/entities")).json()
    values = {item["canonical_value"] for item in entities["items"]}
    assert {"example.com", "203.0.113.10", "example documentation trust"} <= values

    relationships = (await api_client.get(f"/api/v1/cases/{case_id}/relationships")).json()
    types = {item["type"] for item in relationships["items"]}
    assert "RESOLVES_TO" in types
    assert "OWNS_DOMAIN" in types
    assert all(item["confidence_reasons"] for item in relationships["items"])

    # A self-published link is rated highly; nothing claims shared identity.
    link = next(item for item in relationships["items"] if item["type"] == "LINKS_TO")
    assert link["confidence"] >= 0.85
    assert "POSSIBLY_SAME_ENTITY" not in types

    # 9. The graph is renderable and consistent with the entity list.
    graph = (await api_client.get(f"/api/v1/cases/{case_id}/graph")).json()
    assert len(graph["nodes"]) == entities["total"]
    assert len(graph["edges"]) == relationships["total"]
    assert graph["summary"]["entity_type_counts"]["DOMAIN"] >= 1

    filtered = (
        await api_client.get(f"/api/v1/cases/{case_id}/graph", params={"min_confidence": 0.99})
    ).json()
    assert len(filtered["edges"]) < len(graph["edges"])

    # 10. The timeline is built from dated facts only.
    timeline = (await api_client.get(f"/api/v1/cases/{case_id}/timeline")).json()
    assert timeline["summary"]["event_count"] == 1
    assert timeline["events"][0]["occurred_at"].startswith("1995-08-14")

    # 11. The case summary agrees with the detail endpoints.
    summary = (await api_client.get(f"/api/v1/cases/{case_id}/summary")).json()
    assert summary["findings"] == findings["total"]
    assert summary["entities"] == entities["total"]
    assert summary["relationships"] == relationships["total"]
    assert sorted(summary["collectors_run"]) == sorted(by_collector)
    assert summary["case"]["status"] == "COMPLETE"

    # 12. Every report format renders, and every claim cites its evidence.
    html = await api_client.get(f"/api/v1/cases/{case_id}/report")
    assert html.status_code == 200
    assert "Example Domain Investigation" in html.text
    assert "Limitations" in html.text
    assert "AKIA1234567890ABCDEF" not in html.text
    assert "sha256:" in html.text
    # The report is honest about what did not run.
    assert "failed" in html.text.lower()
    assert "skipped" in html.text.lower() or "SEARCH_PROVIDER" in html.text

    markdown = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "md"})
    assert markdown.text.startswith("# Example Domain Investigation")

    payload = (
        await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    ).json()
    assert payload["counts"]["findings"] == findings["total"]
    assert payload["evidence"]
    for finding in payload["findings"]:
        assert finding["evidence"], f"{finding['title']} cites no evidence"
    assert "AKIA1234567890ABCDEF" not in json.dumps(payload)

    # 13. Re-running is idempotent: no duplicate findings, no duplicate events.
    second = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert second.status_code == 202
    assert second.json()["job"]["result"]["findings_created"] == 0
    assert second.json()["job"]["result"]["findings_duplicate"] == 4

    after = (await api_client.get(f"/api/v1/cases/{case_id}/findings")).json()
    assert after["total"] == findings["total"]
    after_timeline = (await api_client.get(f"/api/v1/cases/{case_id}/timeline")).json()
    assert after_timeline["summary"]["event_count"] == 1


async def test_a_case_with_no_targets_completes_without_claiming_anything(api_client):
    created = await api_client.post("/api/v1/cases", json={"name": "Empty case"})
    case_id = created.json()["id"]

    run = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert run.status_code == 202
    assert run.json()["job"]["state"] == "COMPLETE"

    report = await api_client.get(f"/api/v1/cases/{case_id}/report", params={"format": "json"})
    payload = report.json()
    assert payload["counts"]["findings"] == 0
    summary = " ".join(payload["executive_summary"])
    assert "No findings" in summary
    assert "negative result" in summary
