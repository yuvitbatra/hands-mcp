from types import SimpleNamespace

import pytest

import hands.plugins as plugins_mod
from hands.config import HandsConfig
from hands.container import Container
from hands.plugins import ENTRY_POINT_GROUP, PluginManager
from hands.plugins.api import HandsPlugin, PluginContext
from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy
from hands.services.screenshot import ScreenshotService


class GoodPlugin:
    name, version = "good", "1.0.0"
    torn_down = False

    def setup(self, ctx: PluginContext) -> None:
        from pydantic import BaseModel

        class NoArgs(BaseModel, extra="forbid"):
            pass

        async def ping(args, ctx_):
            return {"pong": True}

        ctx.registry.register(ToolSpec(
            "plugin_ping", "plugin-provided tool", NoArgs, ping,
            "read", RetryPolicy.read(), idempotent=True))
        # DI lookup works
        assert isinstance(ctx.service(ScreenshotService),
                          ScreenshotService)

    def teardown(self) -> None:
        GoodPlugin.torn_down = True


class BrokenPlugin:
    name, version = "broken", "1.0.0"

    def setup(self, ctx) -> None:
        raise RuntimeError("boom")

    def teardown(self) -> None:
        pass


class _FakeEntryPoint:
    def __init__(self, name, cls):
        self.name = name
        self._cls = cls

    def load(self):
        return self._cls


def _patch_entry_points(monkeypatch, *eps):
    monkeypatch.setattr(
        plugins_mod, "entry_points",
        lambda group: list(eps) if group == ENTRY_POINT_GROUP else [])


@pytest.fixture
def container():
    cfg = HandsConfig()
    cfg.driver = "fake"
    return Container.build(cfg)


def test_plugin_registers_tool_and_gets_services(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    assert container.registry.get("plugin_ping").name == "plugin_ping"
    assert len(container.plugins.loaded) == 1


def test_broken_plugin_is_skipped_not_fatal(monkeypatch, container):
    _patch_entry_points(monkeypatch,
                        _FakeEntryPoint("broken", BrokenPlugin),
                        _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    assert [p.name for p in container.plugins.loaded] == ["good"]


def test_allowlist_refuses_unlisted(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(["other-plugin"])
    assert container.plugins.loaded == []


def test_teardown_all_reverse_and_contained(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    GoodPlugin.torn_down = False
    container.plugins.teardown_all()
    assert GoodPlugin.torn_down is True


def test_protocol_runtime_checkable():
    assert isinstance(GoodPlugin(), HandsPlugin)


def test_unknown_service_lookup_raises(container):
    ctx = container._plugin_ctx(GoodPlugin())

    class NotAService:
        pass

    with pytest.raises(LookupError):
        ctx.service(NotAService)
