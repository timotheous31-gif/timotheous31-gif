"""Evidence store: content addressing, deduplication and integrity."""

from __future__ import annotations

import uuid

import pytest

from app.models import Case, Finding
from app.models.enums import FindingKind
from app.services.evidence import EvidenceStore, canonical_bytes, sha256_of


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    reset_settings_cache()
    yield EvidenceStore()
    reset_settings_cache()


@pytest.fixture
def case(db_session):
    row = Case(name="Evidence test")
    db_session.add(row)
    db_session.flush()
    return row


def store_payload(store, session, case, content, **kwargs):
    return store.store(
        session,
        case_id=case.id,
        collector=kwargs.pop("collector", "dns"),
        source_url=kwargs.pop("source_url", "https://example.com/source"),
        content=content,
        **kwargs,
    )


def test_hashing_is_stable_regardless_of_key_order():
    assert sha256_of({"a": 1, "b": 2}) == sha256_of({"b": 2, "a": 1})
    assert len(sha256_of({"a": 1})) == 64


def test_canonical_bytes_handles_each_payload_shape():
    assert canonical_bytes(b"raw") == b"raw"
    assert canonical_bytes("text") == b"text"
    assert canonical_bytes({"a": 1}) == b'{"a": 1}'


def test_store_records_provenance(store, db_session, case):
    stored = store_payload(store, db_session, case, {"records": ["93.184.215.14"]})
    evidence = stored.evidence
    assert stored.created is True
    assert evidence.collector == "dns"
    assert evidence.source_url == "https://example.com/source"
    assert evidence.sha256 == stored.sha256
    assert evidence.size_bytes > 0
    assert evidence.retrieved_at is not None


def test_raw_payload_is_written_and_readable(store, db_session, case):
    stored = store_payload(store, db_session, case, {"a": 1})
    path = store.path_for(case.id, stored.sha256)
    assert path.exists()
    assert store.read_raw(case.id, stored.sha256) == path.read_bytes()


def test_identical_payloads_are_deduplicated(store, db_session, case):
    first = store_payload(store, db_session, case, {"a": 1})
    second = store_payload(store, db_session, case, {"a": 1})
    assert second.created is False
    assert second.evidence.id == first.evidence.id


def test_different_payloads_are_distinct(store, db_session, case):
    first = store_payload(store, db_session, case, {"a": 1})
    second = store_payload(store, db_session, case, {"a": 2})
    assert first.sha256 != second.sha256
    assert second.created is True


def test_credentials_never_reach_the_evidence_directory(store, db_session, case):
    stored = store_payload(
        store, db_session, case, {"config": "AKIA1234567890ABCDEF", "host": "example.com"}
    )
    on_disk = store.read_raw(case.id, stored.sha256).decode()
    assert "AKIA1234567890ABCDEF" not in on_disk
    assert "example.com" in on_disk
    assert stored.evidence.redacted is True
    assert "AKIA1234567890ABCDEF" not in (stored.evidence.excerpt or "")


def test_credentials_in_string_payloads_are_removed(store, db_session, case):
    stored = store_payload(store, db_session, case, "token ghp_" + "b" * 36)
    assert "ghp_" not in store.read_raw(case.id, stored.sha256).decode()
    assert stored.evidence.redacted is True


def test_list_payloads_are_filtered(store, db_session, case):
    stored = store_payload(store, db_session, case, [{"secret": "abcd1234abcd1234abcd"}])
    assert "abcd1234abcd1234abcd" not in store.read_raw(case.id, stored.sha256).decode()


def test_evidence_links_to_its_finding(store, db_session, case):
    finding = Finding(
        case_id=case.id,
        kind=FindingKind.DNS_RECORD,
        title="t",
        data={},
        collector="dns",
        dedupe_key="k",
    )
    db_session.add(finding)
    db_session.flush()
    stored = store_payload(store, db_session, case, {"a": 1}, finding=finding)
    assert stored.evidence.finding_id == finding.id


def test_verification_detects_tampering(store, db_session, case):
    stored = store_payload(store, db_session, case, {"a": 1})
    assert store.verify(stored.evidence) is True

    store.path_for(case.id, stored.sha256).write_bytes(b'{"a": 999}')
    assert store.verify(stored.evidence) is False


def test_case_verification_summary(store, db_session, case):
    store_payload(store, db_session, case, {"a": 1})
    second = store_payload(store, db_session, case, {"a": 2})
    summary = store.verify_case(db_session, case.id)
    assert summary["total"] == 2
    assert summary["verified"] == 2
    assert summary["intact"] is True

    store.path_for(case.id, second.sha256).write_bytes(b"tampered")
    after = store.verify_case(db_session, case.id)
    assert after["intact"] is False
    assert second.sha256 in after["mismatched"]


def test_missing_raw_file_is_reported_not_fatal(store, db_session, case):
    stored = store_payload(store, db_session, case, {"a": 1})
    store.path_for(case.id, stored.sha256).unlink()
    summary = store.verify_case(db_session, case.id)
    assert summary["missing_raw"] == 1
    assert store.verify(stored.evidence) is False
    assert store.read_raw(case.id, stored.sha256) is None


def test_oversize_payloads_are_truncated_and_still_hashed(store, db_session, case):
    from app.services.evidence import MAX_STORED_BYTES

    stored = store_payload(store, db_session, case, {"blob": "x" * (MAX_STORED_BYTES + 1000)})
    assert stored.evidence.size_bytes <= MAX_STORED_BYTES
    assert store.verify(stored.evidence) is True


def test_raw_storage_can_be_disabled(db_session, case, tmp_path, monkeypatch):
    from app.core.settings import reset_settings_cache

    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "off"))
    monkeypatch.setenv("EVIDENCE_STORE_RAW", "false")
    reset_settings_cache()
    try:
        store = EvidenceStore()
        stored = store.store(
            db_session,
            case_id=case.id,
            collector="dns",
            source_url=None,
            content={"a": 1},
        )
        assert stored.evidence.raw_ref is None
        assert stored.evidence.sha256
        assert not (tmp_path / "off").exists()
    finally:
        reset_settings_cache()


def test_unknown_case_verification_is_empty(store, db_session):
    assert store.verify_case(db_session, uuid.uuid4())["total"] == 0
