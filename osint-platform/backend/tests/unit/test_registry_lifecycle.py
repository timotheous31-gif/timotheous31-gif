"""Registry lifecycle: deterministic, idempotent, restorable.

`@register_collector` fires at module import, and a module imports once per
process. Registration must therefore not depend on Python's module cache: these
tests pin the behaviour that made an investigation's collector set depend on
which code had happened to run first.
"""

from __future__ import annotations

import pytest

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.registry import (
    _CATALOGUE,
    _REGISTRY,
    BUILTIN_MODULES,
    all_collector_classes,
    builtin_collector_classes,
    load_builtin_collectors,
    plan_collectors,
    register_collector,
    reset_registry,
)
from app.core.errors import ConfigurationError
from app.models.enums import TargetType


@pytest.fixture
def restore_registry():
    """Put the live registry back exactly as it was, whatever a test does."""
    original = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


def builtin_names() -> set[str]:
    return set(builtin_collector_classes())


class TestLoading:
    def test_loading_registers_every_builtin(self, restore_registry):
        load_builtin_collectors()
        assert builtin_names() <= set(_REGISTRY)
        assert len(builtin_names()) == len(BUILTIN_MODULES)

    def test_loading_is_idempotent(self, restore_registry):
        load_builtin_collectors()
        first = dict(_REGISTRY)
        load_builtin_collectors()
        load_builtin_collectors()
        assert first == _REGISTRY

    def test_loading_restores_the_registry_after_a_reset(self, restore_registry):
        """The regression: a reset used to be permanent within a process.

        `load_builtin_collectors()` imported modules whose decorators had
        already run, so it silently did nothing and the registry stayed empty.
        """
        load_builtin_collectors()
        expected = builtin_names()

        reset_registry()
        assert _REGISTRY == {}

        load_builtin_collectors()
        assert expected <= set(_REGISTRY)

    def test_repeated_reset_and_reload_cycles_converge(self, restore_registry):
        """Every cycle lands on the same built-in set.

        Only the built-ins are compared: a reset legitimately drops anything
        else the process had registered, and reloading does not resurrect it.
        """

        def builtins_in_registry() -> dict:
            return {name: cls for name, cls in _REGISTRY.items() if name in builtin_names()}

        reset_registry()
        load_builtin_collectors()
        expected = builtins_in_registry()
        assert expected, "the built-ins should be present after a load"

        for _ in range(3):
            reset_registry()
            load_builtin_collectors()
            assert builtins_in_registry() == expected

    def test_reset_keeps_the_catalogue(self, restore_registry):
        load_builtin_collectors()
        before = dict(_CATALOGUE)
        reset_registry()
        assert before == _CATALOGUE, "the catalogue is a fact about the codebase"

    def test_reload_restores_rate_limits(self, restore_registry):
        from app.core.http import get_limiter

        load_builtin_collectors()
        reset_registry()
        get_limiter().reset()
        load_builtin_collectors()

        dns = _REGISTRY["dns"]
        assert get_limiter().limit_for("dns").requests == dns.rate_limit.requests

    def test_planning_works_again_after_a_reset(self, restore_registry):
        """The user-visible consequence: an investigation plans nothing."""
        reset_registry()
        assert plan_collectors(TargetType.DOMAIN) == []

        load_builtin_collectors()
        planned = {collector.name for collector in plan_collectors(TargetType.DOMAIN)}
        assert {"dns", "rdap", "http_meta"} <= planned


class TestSubstitution:
    def test_a_substituted_registry_is_what_planning_uses(self, restore_registry):
        class _Stub(BaseCollector):
            name = "lifecycle_stub"
            supported_targets = [TargetType.DOMAIN]
            source_attribution = "Stub source"

            async def collect(self, target, ctx):
                return CollectorResult()

        reset_registry()
        _REGISTRY[_Stub.name] = _Stub

        assert [c.name for c in plan_collectors(TargetType.DOMAIN)] == ["lifecycle_stub"]
        assert [cls.name for cls in all_collector_classes()] == ["lifecycle_stub"]

    def test_builtins_stay_discoverable_behind_a_substituted_registry(self, restore_registry):
        reset_registry()
        assert len(builtin_collector_classes()) == len(BUILTIN_MODULES)


class TestReportingDoesNotTouchTheRegistry:
    """The defect this suite exists for: rendering a report changed the plan.

    `_sources()` used to call `load_builtin_collectors()` to look up source
    attribution. In a process where the collector modules had not yet been
    imported, that first import registered all nine built-ins mid-request, so
    the next investigation planned a different — and larger — set of collectors
    than the one the caller had configured.
    """

    def test_building_a_report_leaves_the_registry_untouched(self, db_session, restore_registry):
        from app.models import Case
        from app.reporting import build_report

        case = Case(name="Registry isolation")
        db_session.add(case)
        db_session.commit()

        class _Stub(BaseCollector):
            name = "report_isolation_stub"
            supported_targets = [TargetType.DOMAIN]

            async def collect(self, target, ctx):
                return CollectorResult()

        reset_registry()
        _REGISTRY[_Stub.name] = _Stub
        before = dict(_REGISTRY)

        build_report(db_session, case.id)

        assert before == _REGISTRY, "rendering a report must not change the collector set"

    def test_attribution_comes_from_the_run_row_not_the_registry(
        self, db_session, restore_registry
    ):
        """A report can be rendered by a process with no collectors loaded."""
        from app.models import Case, CollectorRun, Target
        from app.models.enums import RunStatus
        from app.reporting import build_report

        case = Case(name="Recorded attribution")
        db_session.add(case)
        db_session.flush()
        target = Target(
            case_id=case.id,
            type=TargetType.DOMAIN,
            raw_input="example.com",
            normalized_value="example.com",
        )
        db_session.add(target)
        db_session.flush()
        db_session.add(
            CollectorRun(
                case_id=case.id,
                target_id=target.id,
                collector="dns",
                collector_version="1.0.0",
                source_attribution="Public DNS (system resolvers)",
                status=RunStatus.SUCCESS,
            )
        )
        db_session.commit()

        reset_registry()
        model = build_report(db_session, case.id)

        source = next(item for item in model.sources if item.collector == "dns")
        assert source.attribution == "Public DNS (system resolvers)"
        assert _REGISTRY == {}


class TestValidation:
    def test_a_duplicate_name_is_refused_even_after_a_reset(self, restore_registry):
        """The catalogue keeps names unique for the life of the process."""

        @register_collector
        class _First(BaseCollector):
            name = "lifecycle_dup"
            supported_targets = [TargetType.DOMAIN]

            async def collect(self, target, ctx):
                return CollectorResult()

        reset_registry()

        with pytest.raises(ConfigurationError, match="already registered"):

            @register_collector
            class _Second(BaseCollector):
                name = "lifecycle_dup"
                supported_targets = [TargetType.DOMAIN]

                async def collect(self, target, ctx):
                    return CollectorResult()

        _CATALOGUE.pop("lifecycle_dup", None)

    def test_re_registering_the_same_class_is_allowed(self, restore_registry):
        class _Idem(BaseCollector):
            name = "lifecycle_idempotent"
            supported_targets = [TargetType.DOMAIN]

            async def collect(self, target, ctx):
                return CollectorResult()

        try:
            register_collector(_Idem)
            register_collector(_Idem)
            assert _REGISTRY["lifecycle_idempotent"] is _Idem
        finally:
            _CATALOGUE.pop("lifecycle_idempotent", None)
            _REGISTRY.pop("lifecycle_idempotent", None)
