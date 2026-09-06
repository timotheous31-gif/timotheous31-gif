"""Collector registry.

Adding a source is one decorator away::

    @register_collector
    class ExampleCollector(BaseCollector):
        name = "example"
        supported_targets = [TargetType.DOMAIN]
        ...

The registry is also the planner: given a target type it returns the collectors
that accept it, minus anything the caller excluded or that is unavailable.

Two dictionaries, deliberately:

``_CATALOGUE``
    Every collector class the decorator has ever seen in this process. It is a
    static fact about the codebase and is never cleared.

``_REGISTRY``
    The live set an investigation plans from. Normally identical to the
    catalogue; tests and embedders may substitute it.

The split exists because ``@register_collector`` fires at *module import* time,
and a module imports only once per process. Without a permanent catalogue,
:func:`load_builtin_collectors` would be a no-op on its second call and could
never restore the registry after :func:`reset_registry` — registration would
depend on Python's module cache rather than on the call itself.
"""

from __future__ import annotations

import importlib

from app.collectors.base import BaseCollector
from app.core.errors import ConfigurationError
from app.core.http import register_provider
from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.models.enums import TargetType

log = get_logger(__name__)

#: Module names under ``app.collectors`` that declare a built-in collector.
BUILTIN_MODULES: tuple[str, ...] = (
    "ctlog",
    "dns",
    "email",
    "github",
    "http_meta",
    "rdap",
    "search",
    "username",
    "wayback",
)

#: Every collector class declared in this process. Never cleared.
_CATALOGUE: dict[str, type[BaseCollector]] = {}

#: The live set used for planning. Substitutable.
_REGISTRY: dict[str, type[BaseCollector]] = {}


def register_collector(cls: type[BaseCollector]) -> type[BaseCollector]:
    """Class decorator registering a collector under its ``name``."""
    _validate(cls)
    existing = _REGISTRY.get(cls.name) or _CATALOGUE.get(cls.name)
    if existing is not None and existing is not cls:
        raise ConfigurationError(
            f"Collector name {cls.name!r} is already registered by {existing.__name__}"
        )
    _CATALOGUE[cls.name] = cls
    _activate(cls)
    return cls


def _validate(cls: type[BaseCollector]) -> None:
    if not cls.name:
        raise ConfigurationError(f"{cls.__name__} must declare a non-empty `name`")
    if not cls.supported_targets:
        raise ConfigurationError(f"{cls.__name__} must declare `supported_targets`")


def _activate(cls: type[BaseCollector]) -> None:
    """Put ``cls`` into the live registry and declare its rate limit."""
    _REGISTRY[cls.name] = cls
    register_provider(cls.name, cls.rate_limit)


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
        collector = cls(settings)
        available, reason = collector.is_available()
        entries.append(
            {
                **cls.metadata(),
                "available": available,
                "unavailable_reason": reason,
                "configuration": collector.configuration().as_dict(),
            }
        )
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


def builtin_collector_classes() -> dict[str, type[BaseCollector]]:
    """Every built-in collector class, importing the modules if needed.

    Reads the catalogue rather than the live registry, so a substituted
    registry does not hide the built-ins from callers that legitimately want
    the full set (the ``/collectors`` catalogue, the CLI listing).
    """
    for module in BUILTIN_MODULES:
        importlib.import_module(f"app.collectors.{module}")
    prefixes = {f"app.collectors.{module}" for module in BUILTIN_MODULES}
    return {name: cls for name, cls in _CATALOGUE.items() if cls.__module__ in prefixes}


def load_builtin_collectors() -> None:
    """Ensure every built-in collector is in the live registry.

    Idempotent, and independent of the module cache: the import only has an
    effect the first time, so activation is re-applied from the catalogue on
    every call. That is what makes the function able to restore the registry
    after :func:`reset_registry`.
    """
    for name, cls in builtin_collector_classes().items():
        if _REGISTRY.get(name) is not cls:
            _activate(cls)


def reset_registry() -> None:
    """Clear the live registry (tests and embedders).

    The catalogue survives, so :func:`load_builtin_collectors` can put the
    built-ins back.
    """
    _REGISTRY.clear()
