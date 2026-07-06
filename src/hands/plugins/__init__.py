"""Entry-point plugin discovery (DESIGN §11)."""
from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points

import structlog

from .api import HandsPlugin, PluginContext

ENTRY_POINT_GROUP = "hands.plugins"
log = structlog.get_logger(__name__)


class PluginManager:
    def __init__(self, ctx_factory: Callable[[HandsPlugin],
                                             PluginContext]) -> None:
        self._ctx_factory = ctx_factory
        self.loaded: list[HandsPlugin] = []

    def discover_and_load(self, allowlist: list[str] | None) -> None:
        """A broken plugin logs and is skipped — it must never take the
        server down. With an allowlist, unknown entry points are refused
        (DESIGN §13.7)."""
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if allowlist is not None and ep.name not in allowlist:
                log.warning("plugin_skipped_not_allowlisted",
                            name=ep.name)
                continue
            try:
                plugin: HandsPlugin = ep.load()()
                plugin.setup(self._ctx_factory(plugin))
                self.loaded.append(plugin)
                log.info("plugin_loaded", name=plugin.name,
                         version=plugin.version)
            except Exception:
                log.exception("plugin_failed", name=ep.name)

    def teardown_all(self) -> None:
        for plugin in reversed(self.loaded):
            try:
                plugin.teardown()
            except Exception:
                log.exception("plugin_teardown_failed", name=plugin.name)
        self.loaded.clear()
