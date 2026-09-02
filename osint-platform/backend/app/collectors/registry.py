"""Collector registry.

Adding a source is one decorator away::

    @register_collector
    class ExampleCollector(BaseCollector):
        name = "example"
        supported_targets = [TargetType.DOMAIN]
        ...

The registry is also the planner: given a target type it returns the collectors
that accept it, minus anything the caller excluded or that is unavailable.
"""

from __future__ import annotations

from app.collectors.base import BaseCollector
from app.core.errors import ConfigurationError
from app.core.http import register_provider
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.enums import TargetType

log = get_logger(__name__)

_REGISTRY: dict[str, type[BaseCollector]] = {}


def register_collector(cls: type[BaseCollector]) -> type[BaseCollector]:
    """Class decorator registering a collector under its ``name``."""
    if not cls.name:
        raise ConfigurationError(f"{cls.__name__} must declare a non-empty `name`")
    if not cls.supported_targets:
        raise ConfigurationError(f"{cls.__name__} must declare `supported_targets`")
    existing = _REGISTRY.get(cls.name)
    if existing is not None and existing is not cls:
        raise ConfigurationError(
            f"Collector name {cls.name!r} is already registered by {existing.__name__}"
        )
    _REGISTRY[cls.name] = cls
    register_provider(cls.name, cls.rate_limit)
    return cls


def get_collector_class(name: str) -> type[BaseCollector]:
    """Look up a collector class by name."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown collector {name!r}") from exc


def all_collector_classes() -> list[type[BaseCollector]]:
    """Every registered collector, ordered by name."""
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def collector_metadata() -> list[dict]:
    """Registry metadata for the API and CLI."""
    settings = get_settings()
    entries = []
    for cls in all_collector_classes():
        available, reason = cls(settings).is_available()
        entries.append({**cls.metadata(), "available": available, "unavailable_reason": reason})
    return entries


def plan_collectors(
    target_type: TargetType,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    settings: Settings | None = None,
    skip_unavailable: bool = False,
) -> list[BaseCollector]:
    """Instantiate the collectors that should run for ``target_type``.

    Args:
        include: when given, restrict the plan to these collector names.
        exclude: names to drop from the plan.
        skip_unavailable: drop collectors whose credentials are missing instead
            of returning them (the engine keeps them so it can record a
            ``SKIPPED`` run with the reason).
    """
    settings = settings or get_settings()
    include_set = {name.strip().lower() for name in include or [] if name.strip()}
    exclude_set = {name.strip().lower() for name in exclude or [] if name.strip()}

    unknown = (include_set | exclude_set) - set(_REGISTRY)
    if unknown:
        raise ConfigurationError(
            f"Unknown collector(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )

    planned: list[BaseCollector] = []
    for cls in all_collector_classes():
        if not cls.accepts(target_type):
            continue
        if include_set and cls.name not in include_set:
            continue
        if cls.name in exclude_set:
            continue
        collector = cls(settings)
        if skip_unavailable and not collector.is_available()[0]:
            continue
        planned.append(collector)
    return planned


def load_builtin_collectors() -> None:
    """Import every built-in collector module so the decorators run."""
    from app.collectors import (  # noqa: F401 - imported for their side effects
        dns,
        http_meta,
        rdap,
    )


def reset_registry() -> None:
    """Clear the registry (tests only)."""
    _REGISTRY.clear()
