"""The investigation API: running a case and reading everything it produced."""

from __future__ import annotations

import pytest

from app.collectors.base import BaseCollector, CollectorResult, FindingDraft, RawPayload
from app.collectors.registry import _REGISTRY
from app.models.enums import FindingKind, TargetType


class StubDomain(BaseCollector):
    name = "stub_domain"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        from datetime import UTC, datetime

        result = CollectorResult()
        payload = RawPayload(
            source_url=f"dns://{target.value}/A",
            content={"hostname": target.value, "records": ["93.184.215.14"]},
        )
        result.add(
            FindingDraft(
                kind=FindingKind.DNS_RECORD,
                title=f"DNS A for {target.value}",
                data={"hostname": target.value, "record_type": "A", "records": ["93.184.215.14"]},
                confidence=0.95,
                confidence_reasons=["Resolved from public DNS"],
                observed_at=datetime(2020, 1, 1, tzinfo=UTC),
                dedupe_key=f"dns:{target.value}:A",
            ),
            payload,
        )
        return result


@pytest.fixture(autouse=True)
def stubs(monkeypatch, tmp_path):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setattr("app.services.engine.load_builtin_collectors", lambda: None)
    reset_settings_cache()

    original = dict(_REGISTRY)
    _REGISTRY.clear()
    _REGISTRY[StubDomain.name] = StubDomain
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)
    reset_settings_cache()


@pytest.fixture
async def investigated(api_client, case_id):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    response = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert response.status_code == 202, response.text
    return case_id, response.json()


async def test_run_returns_a_job_and_says_how_it_dispatched(investigated):
    _, body = investigated
    assert body["job"]["state"] in {"COMPLETE", "RUNNING", "QUEUED"}
    assert body["dispatch"]["dispatched"] == "inline"


async def test_findings_are_listed_with_evidence(api_client, investigated):
    case_id, _ = investigated
    response = await api_client.get(f"/api/v1/cases/{case_id}/findings")
    body = response.json()
    assert body["total"] == 1
    finding = body["items"][0]
    assert finding["kind"] == "DNS_RECORD"
    assert finding["confidence_reasons"]
    assert finding["classification"] == "PUBLIC"
    assert finding["evidence"]
    assert len(finding["evidence"][0]["sha256"]) == 64


async def test_findings_can_be_filtered(api_client, investigated):
    case_id, _ = investigated
    by_kind = await api_client.get(
        f"/api/v1/cases/{case_id}/findings", params={"kind": "DNS_RECORD"}
    )
    assert by_kind.json()["total"] == 1

    other_kind = await api_client.get(
        f"/api/v1/cases/{case_id}/findings", params={"kind": "CERTIFICATE"}
    )
    assert other_kind.json()["total"] == 0

    high = await api_client.get(
        f"/api/v1/cases/{case_id}/findings", params={"min_confidence": 0.99}
    )
    assert high.json()["total"] == 0


async def test_entities_and_relationships_are_exposed(api_client, investigated):
    case_id, _ = investigated
    entities = (await api_client.get(f"/api/v1/cases/{case_id}/entities")).json()
    assert entities["total"] == 2
    assert {item["canonical_value"] for item in entities["items"]} == {
        "example.com",
        "93.184.215.14",
    }
    assert all(item["source_finding_ids"] for item in entities["items"])

    relationships = (await api_client.get(f"/api/v1/cases/{case_id}/relationships")).json()
    assert relationships["total"] == 1
    edge = relationships["items"][0]
    assert edge["type"] == "RESOLVES_TO"
    assert edge["confidence_reasons"]
    assert edge["source_label"] and edge["target_label"]
    assert edge["evidence_finding_ids"]


async def test_graph_is_frontend_ready(api_client, investigated):
    case_id, _ = investigated
    graph = (await api_client.get(f"/api/v1/cases/{case_id}/graph")).json()
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["stats"]["node_count"] == 2
    assert graph["summary"]["entity_type_counts"]["DOMAIN"] == 1
    assert graph["edges"][0]["reasons"]


async def test_graph_confidence_filter(api_client, investigated):
    case_id, _ = investigated
    filtered = (
        await api_client.get(f"/api/v1/cases/{case_id}/graph", params={"min_confidence": 0.99})
    ).json()
    assert filtered["edges"] == []
    assert len(filtered["nodes"]) == 2


async def test_graph_entity_type_filter(api_client, investigated):
    case_id, _ = investigated
    filtered = (
        await api_client.get(f"/api/v1/cases/{case_id}/graph", params={"types": ["DOMAIN"]})
    ).json()
    assert [node["type"] for node in filtered["nodes"]] == ["DOMAIN"]


async def test_timeline_is_ordered_and_summarised(api_client, investigated):
    case_id, _ = investigated
    timeline = (await api_client.get(f"/api/v1/cases/{case_id}/timeline")).json()
    assert timeline["summary"]["event_count"] == 1
    assert timeline["events"][0]["occurred_at"].startswith("2020-01-01")
    assert timeline["events"][0]["kind"] == "dns_record"


async def test_evidence_listing_and_verification(api_client, investigated):
    case_id, _ = investigated
    evidence = (await api_client.get(f"/api/v1/cases/{case_id}/evidence")).json()
    assert evidence["total"] == 1
    assert evidence["items"][0]["collector"] == "stub_domain"

    verification = (await api_client.get(f"/api/v1/cases/{case_id}/evidence/verify")).json()
    assert verification["intact"] is True
    assert verification["verified"] == 1


async def test_collector_runs_are_visible(api_client, investigated):
    case_id, _ = investigated
    runs = (await api_client.get(f"/api/v1/cases/{case_id}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["collector"] == "stub_domain"
    assert runs[0]["status"] == "SUCCESS"
    assert runs[0]["duration_ms"] >= 0


async def test_case_summary_reflects_the_run(api_client, investigated):
    case_id, _ = investigated
    summary = (await api_client.get(f"/api/v1/cases/{case_id}/summary")).json()
    assert summary["findings"] == 1
    assert summary["entities"] == 2
    assert summary["collectors_run"] == ["stub_domain"]
    assert summary["confidence_distribution"]["high"] == 1


async def test_job_status_is_readable(api_client, investigated):
    case_id, body = investigated
    job_id = body["job"]["id"]
    job = (await api_client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["state"] == "COMPLETE"
    assert job["progress"] == 1.0
    assert job["result"]["findings_created"] == 1

    listing = (await api_client.get(f"/api/v1/cases/{case_id}/jobs")).json()
    assert len(listing) == 1


async def test_a_second_concurrent_run_is_rejected(api_client, case_id, monkeypatch):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    monkeypatch.setattr(
        "app.services.jobs.dispatch_job", lambda session, job: {"dispatched": "test"}
    )
    first = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert first.status_code == 202
    second = await api_client.post(f"/api/v1/cases/{case_id}/run", json={})
    assert second.status_code == 409


async def test_cancelling_a_queued_job(api_client, case_id, monkeypatch):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    monkeypatch.setattr(
        "app.services.jobs.dispatch_job", lambda session, job: {"dispatched": "test"}
    )
    job_id = (await api_client.post(f"/api/v1/cases/{case_id}/run", json={})).json()["job"]["id"]

    cancelled = await api_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLED"

    again = await api_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert again.status_code == 409


async def test_collector_selection_through_the_api(api_client, case_id):
    await api_client.post(f"/api/v1/cases/{case_id}/targets", json={"value": "example.com"})
    response = await api_client.post(
        f"/api/v1/cases/{case_id}/run", json={"collectors": ["stub_domain"]}
    )
    assert response.status_code == 202
    runs = (await api_client.get(f"/api/v1/cases/{case_id}/runs")).json()
    assert {run["collector"] for run in runs} == {"stub_domain"}


async def test_unknown_job_returns_404(api_client):
    response = await api_client.get("/api/v1/jobs/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404


async def test_collector_catalogue_lists_availability(api_client):
    catalogue = (await api_client.get("/api/v1/collectors")).json()
    names = {entry["name"] for entry in catalogue}
    assert "stub_domain" in names
    assert all("available" in entry for entry in catalogue)
