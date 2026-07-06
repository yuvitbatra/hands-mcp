"""The ONLY stable import surface for plugin authors (DESIGN §6.4).
Semver-guarded from M4 on: additive changes only within a major version."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class PluginContext:
    def __init__(self, registry, config: Mapping[str, Any], logger,
                 services: Mapping[type, object]) -> None:
        self.registry = registry
        self.config = config
        self.logger = logger
        self._services = services

    def service(self, proto: type[T]) -> T:
        try:
            return self._services[proto]        # type: ignore[return-value]
        except KeyError:
            raise LookupError(
                f"no service registered for {proto.__name__}") from None


@runtime_checkable
class HandsPlugin(Protocol):
    name: str
    version: str

    def setup(self, ctx: PluginContext) -> None: ...
    def teardown(self) -> None: ...
