"""Runs collectors with isolation, timeouts and per-run bookkeeping.

The rule this module exists to enforce: **one failing collector must never
abort an investigation**. Each collector runs as its own task with its own
timeout; failures are captured, typed and returned as ``RunOutcome`` records so
the engine can persist them and carry on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.collectors.base import BaseCollector, CollectorContext, CollectorResult
from app.core.errors import CollectorTimeout, CollectorUnavailable, OsintError
from app.core.logging import collector_var, get_logger
from app.models.enums import RunStatus
from app.services.normalization import NormalizedTarget

log = get_logger(__name__)

#: Called with (collector_name, completed, total) as each collector finishes.
ProgressCallback = Callable[[str, int, int], Awaitable[None] | None]


@dataclass(slots=True)
class RunOutcome:
    """The result of running one collector against one target."""

    collector: str
    version: str
    attribution: str
    status: RunStatus
    result: CollectorResult | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status in {RunStatus.SUCCESS, RunStatus.PARTIAL}

    @property
    def findings(self) -> int:
        return len(self.result.findings) if self.result else 0


async def run_collector(
    collector: BaseCollector, target: NormalizedTarget, ctx: CollectorContext
) -> RunOutcome:
    """Run one collector, converting any failure into a typed outcome."""
    token = collector_var.set(collector.name)
    started = time.perf_counter()
    try:
        collector.ensure_available()
        budget = collector.run_timeout or ctx.settings.collector_timeout_seconds
        result = await asyncio.wait_for(collector.collect(target, ctx), timeout=budget)
    except CollectorUnavailable as exc:
        return _outcome(collector, RunStatus.SKIPPED, started, exc)
    except TimeoutError:
        budget = collector.run_timeout or ctx.settings.collector_timeout_seconds
        log.warning("collector.timeout", collector=collector.name, timeout=budget)
        return _outcome(
            collector,
            RunStatus.TIMEOUT,
            started,
            CollectorTimeout(f"{collector.name} exceeded its {budget}s run budget"),
        )
    except asyncio.CancelledError:
        # Cancellation is the caller's decision, not a collector failure.
        raise
    except OsintError as exc:
        log.warning(
            "collector.failed",
            collector=collector.name,
            error_type=type(exc).__name__,
            error=exc.message,
        )
        return _outcome(collector, RunStatus.FAILED, started, exc)
    except httpx.HTTPError as exc:
        # An unreachable upstream is an expected outcome for a network
        # collector, not a defect: record it without a stack trace.
        log.warning(
            "collector.network_failure",
            collector=collector.name,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return _outcome(collector, RunStatus.FAILED, started, exc)
    except Exception as exc:
        log.exception("collector.unexpected_error", collector=collector.name)
        return _outcome(collector, RunStatus.FAILED, started, exc)
    else:
        status = RunStatus.PARTIAL if result.notes and not result.findings else RunStatus.SUCCESS
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "collector.completed",
            collector=collector.name,
            findings=len(result.findings),
            duration_ms=duration_ms,
        )
        return RunOutcome(
            collector=collector.name,
            version=collector.version,
            attribution=collector.source_attribution,
            status=status,
            result=result,
            duration_ms=duration_ms,
            notes=list(result.notes),
        )
    finally:
        collector_var.reset(token)


def _outcome(
    collector: BaseCollector, status: RunStatus, started: float, exc: BaseException
) -> RunOutcome:
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return RunOutcome(
        collector=collector.name,
        version=collector.version,
        attribution=collector.source_attribution,
        status=status,
        error_type=type(exc).__name__,
        error_message=message[:2000],
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )


async def run_all(
    collectors: list[BaseCollector],
    target: NormalizedTarget,
    ctx: CollectorContext,
    *,
    concurrency: int | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[RunOutcome]:
    """Run ``collectors`` concurrently against one target.

    Args:
        concurrency: maximum collectors in flight (defaults to the setting).
        on_progress: notified as each collector finishes.
        should_cancel: polled before each collector starts; when it returns
            True the remaining collectors are recorded as ``SKIPPED``.
    """
    if not collectors:
        return []

    limit = concurrency or ctx.settings.collector_concurrency
    semaphore = asyncio.Semaphore(max(1, limit))
    total = len(collectors)
    completed = 0
    outcomes: list[RunOutcome] = []
    lock = asyncio.Lock()

    async def _one(collector: BaseCollector) -> RunOutcome:
        nonlocal completed
        if should_cancel is not None and should_cancel():
            outcome = RunOutcome(
                collector=collector.name,
                version=collector.version,
                attribution=collector.source_attribution,
                status=RunStatus.SKIPPED,
                error_type="Cancelled",
                error_message="Investigation cancelled before this collector started",
            )
        else:
            async with semaphore:
                outcome = await run_collector(collector, target, ctx)
        async with lock:
            completed += 1
            current = completed
        if on_progress is not None:
            maybe = on_progress(collector.name, current, total)
            if asyncio.iscoroutine(maybe):
                await maybe
        return outcome

    results = await asyncio.gather(*(_one(collector) for collector in collectors))
    outcomes.extend(results)
    return outcomes
