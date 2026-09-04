"""Collector base contract, registry and runner."""

from __future__ import annotations

import asyncio

import pytest

from app.collectors.base import (
    BaseCollector,
    CollectorResult,
    FindingDraft,
    RawPayload,
)
from app.collectors.registry import (
    collector_metadata,
    get_collector_class,
    plan_collectors,
    register_collector,
)
from app.collectors.runner import run_all, run_collector
from app.core.errors import CollectorError, CollectorUnavailable, ConfigurationError
from app.core.ratelimit import RateLimit
from app.models.enums import FindingKind, RunStatus, TargetType
from app.services.normalization import normalize_target


@pytest.fixture(autouse=True)
def _builtins():
    from app.collectors.registry import load_builtin_collectors

    load_builtin_collectors()


def _draft(title: str = "t") -> FindingDraft:
    return FindingDraft(kind=FindingKind.NOTE, title=title, data={"a": 1})


class _Good(BaseCollector):
    name = "test_good"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        result = CollectorResult()
        result.add(_draft("good"), RawPayload(source_url="https://example.com/", content={}))
        return result


class _Slow(BaseCollector):
    name = "test_slow"
    supported_targets = [TargetType.DOMAIN]
    run_timeout = 0.05

    async def collect(self, target, ctx):
        await asyncio.sleep(2)
        return CollectorResult()


class _Broken(BaseCollector):
    name = "test_broken"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        raise CollectorError("upstream rejected the query")


class _Exploding(BaseCollector):
    name = "test_exploding"
    supported_targets = [TargetType.DOMAIN]

    async def collect(self, target, ctx):
        raise ZeroDivisionError("bug in a collector")


class _NeedsKey(BaseCollector):
    name = "test_needs_key"
    supported_targets = [TargetType.DOMAIN]
    requires_api_key = True

    def is_available(self):
        return False, "TEST_API_KEY is not configured"

    async def collect(self, target, ctx):  # pragma: no cover - never reached
        return CollectorResult()


@pytest.fixture
def target():
    return normalize_target("example.com")


@pytest.fixture
def ctx(collector_ctx):
    return collector_ctx


def test_registry_rejects_nameless_collector():
    with pytest.raises(ConfigurationError, match="name"):

        @register_collector
        class _Nameless(BaseCollector):
            supported_targets = [TargetType.DOMAIN]

            async def collect(self, target, ctx):
                return CollectorResult()


def test_registry_rejects_collector_without_targets():
    with pytest.raises(ConfigurationError, match="supported_targets"):

        @register_collector
        class _NoTargets(BaseCollector):
            name = "no_targets"

            async def collect(self, target, ctx):
                return CollectorResult()


def test_registry_rejects_duplicate_names():
    @register_collector
    class _First(BaseCollector):
        name = "dup_name"
        supported_targets = [TargetType.DOMAIN]

        async def collect(self, target, ctx):
            return CollectorResult()

    with pytest.raises(ConfigurationError, match="already registered"):

        @register_collector
        class _Second(BaseCollector):
            name = "dup_name"
            supported_targets = [TargetType.DOMAIN]

            async def collect(self, target, ctx):
                return CollectorResult()


def test_builtin_collectors_are_registered():
    names = {entry["name"] for entry in collector_metadata()}
    assert {"dns", "rdap", "http_meta"} <= names
    assert get_collector_class("dns").name == "dns"


def test_metadata_reports_declared_attributes():
    entry = next(item for item in collector_metadata() if item["name"] == "dns")
    assert entry["requires_api_key"] is False
    assert "DOMAIN" in entry["supported_targets"]
    assert entry["available"] is True
    assert entry["rate_limit"]


def test_planning_filters_by_target_type():
    for_domain = {c.name for c in plan_collectors(TargetType.DOMAIN)}
    for_username = {c.name for c in plan_collectors(TargetType.USERNAME)}
    assert "dns" in for_domain
    assert "dns" not in for_username


def test_planning_honours_include_and_exclude():
    only_dns = plan_collectors(TargetType.DOMAIN, include=["dns"])
    assert [c.name for c in only_dns] == ["dns"]

    without_dns = {c.name for c in plan_collectors(TargetType.DOMAIN, exclude=["dns"])}
    assert "dns" not in without_dns


def test_planning_rejects_unknown_collector_names():
    with pytest.raises(ConfigurationError, match="Unknown collector"):
        plan_collectors(TargetType.DOMAIN, include=["does_not_exist"])


def test_accepts_helper():
    assert get_collector_class("dns").accepts(TargetType.DOMAIN)
    assert not get_collector_class("dns").accepts(TargetType.ORGANIZATION)


async def test_run_collector_success(target, ctx):
    outcome = await run_collector(_Good(), target, ctx)
    assert outcome.status is RunStatus.SUCCESS
    assert outcome.findings == 1
    assert outcome.duration_ms >= 0


async def test_run_collector_records_domain_error(target, ctx):
    outcome = await run_collector(_Broken(), target, ctx)
    assert outcome.status is RunStatus.FAILED
    assert outcome.error_type == "CollectorError"
    assert "upstream rejected" in outcome.error_message


async def test_run_collector_records_unexpected_error(target, ctx):
    outcome = await run_collector(_Exploding(), target, ctx)
    assert outcome.status is RunStatus.FAILED
    assert outcome.error_type == "ZeroDivisionError"


async def test_run_collector_times_out(target, ctx):
    outcome = await run_collector(_Slow(), target, ctx)
    assert outcome.status is RunStatus.TIMEOUT
    assert outcome.error_type == "CollectorTimeout"


async def test_unavailable_collector_is_skipped_not_failed(target, ctx):
    outcome = await run_collector(_NeedsKey(), target, ctx)
    assert outcome.status is RunStatus.SKIPPED
    assert "TEST_API_KEY" in outcome.error_message


async def test_one_failure_does_not_abort_the_others(target, ctx):
    outcomes = await run_all([_Good(), _Broken(), _Exploding(), _Good()], target, ctx)
    statuses = [outcome.status for outcome in outcomes]
    assert statuses.count(RunStatus.SUCCESS) == 2
    assert statuses.count(RunStatus.FAILED) == 2


async def test_progress_callback_reports_every_collector(target, ctx):
    seen: list[tuple[str, int, int]] = []

    async def on_progress(name: str, done: int, total: int) -> None:
        seen.append((name, done, total))

    await run_all([_Good(), _Broken()], target, ctx, on_progress=on_progress)
    assert len(seen) == 2
    assert seen[-1][1] == 2
    assert {entry[2] for entry in seen} == {2}


async def test_cancellation_skips_remaining_collectors(target, ctx):
    outcomes = await run_all([_Good(), _Good()], target, ctx, should_cancel=lambda: True)
    assert all(outcome.status is RunStatus.SKIPPED for outcome in outcomes)


async def test_empty_plan_returns_nothing(target, ctx):
    assert await run_all([], target, ctx) == []


def test_finding_dedupe_key_is_stable():
    a = FindingDraft(kind=FindingKind.NOTE, title="x", data={"b": 2, "a": 1})
    b = FindingDraft(kind=FindingKind.NOTE, title="different title", data={"a": 1, "b": 2})
    assert a.key("c") == b.key("c")
    assert a.key("c") != a.key("other")


def test_explicit_dedupe_key_wins():
    draft = FindingDraft(kind=FindingKind.NOTE, title="x", data={}, dedupe_key="fixed")
    assert draft.key("c") == "fixed"


def test_result_add_links_payload():
    result = CollectorResult()
    payload = RawPayload(source_url="https://example.com/x", content={"a": 1})
    draft = result.add(_draft(), payload)
    assert draft.payload_index == 0
    assert draft.source_url == "https://example.com/x"
    assert result.payloads == [payload]


def test_ensure_available_raises_for_missing_key():
    with pytest.raises(CollectorUnavailable, match="TEST_API_KEY"):
        _NeedsKey().ensure_available()


def test_rate_limit_is_declared_per_collector():
    assert isinstance(get_collector_class("rdap").rate_limit, RateLimit)
    assert get_collector_class("rdap").rate_limit.rate_per_second <= 5
