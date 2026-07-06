"""Unit tests for Dispatcher.call_unlocked (M4)."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from hands.audit import AuditLogger
from hands.config import HandsConfig
from hands.dispatcher import Dispatcher
from hands.metrics import Metrics
from hands.permissions import Allowed, AllowAllPermissions, Denied
from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy
from hands.state import StateManager

pytestmark = pytest.mark.anyio


class NoArgs(BaseModel, extra="forbid"):
    pass


class XYArgs(BaseModel, extra="forbid"):
    x: int
    y: int


def make_env(tmp_path, specs=None, permissions=None):
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    reg = ToolRegistry()
    for s in (specs or []):
        reg.register(s)
    state = StateManager(cfg)
    disp = Dispatcher(reg, permissions or AllowAllPermissions(), state,
                      AuditLogger(cfg), Metrics(), cfg)
    return disp, state


async def test_call_unlocked_skips_lock_and_policy(tmp_path):
    async def ok_handler(args, ctx):
        return {"answer": 42}

    class DenyEverything:
        def authorize(self, action):
            return Denied("no")

    disp, state = make_env(tmp_path, [
        ToolSpec("mouse_move", "d", XYArgs, ok_handler, "act",
                 RetryPolicy.none())
    ], permissions=DenyEverything())

    # dispatch() is denied...
    res = await disp.dispatch("mouse_move", {"x": 1, "y": 1})
    assert res["ok"] is False

    # ...but call_unlocked bypasses policy
    res = await disp.call_unlocked("mouse_move", {"x": 2, "y": 2})
    assert res["ok"] is True


async def test_call_unlocked_still_validates_and_observes(tmp_path):
    async def ok_handler(args, ctx):
        return {}

    disp, state = make_env(tmp_path, [
        ToolSpec("mouse_move", "d", XYArgs, ok_handler, "act",
                 RetryPolicy.none())
    ])

    # invalid args → validation error
    res = await disp.call_unlocked("mouse_move", {"x": "bad"})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_ARGS"

    # valid args → ok and marks screen dirty
    state.clear_screen_dirty()
    ok = await disp.call_unlocked("mouse_move", {"x": 5, "y": 5})
    assert ok["ok"] is True
    assert state.screen_dirty is True


@pytest.fixture
def dispatcher_env(tmp_path):
    async def ok_handler(args, ctx):
        return {}

    disp, state = make_env(tmp_path, [
        ToolSpec("mouse_move", "d", XYArgs, ok_handler, "act",
                 RetryPolicy.none())
    ])
    return SimpleNamespace(dispatcher=disp, state=state)


async def test_needs_confirmation_declined_is_policy_denied(dispatcher_env):
    from hands.permissions import NeedsConfirmation

    class Confirming:
        def authorize(self, action):
            return NeedsConfirmation("really?")

        async def confirm(self, prompt, action):
            return False

    env = dispatcher_env
    env.dispatcher._permissions = Confirming()
    res = await env.dispatcher.dispatch("mouse_move", {"x": 1, "y": 1})
    assert res["ok"] is False
    assert res["error"]["code"] == "POLICY_DENIED"


async def test_rate_limit_denies_burst(dispatcher_env):
    env = dispatcher_env
    env.dispatcher._config.security.max_actions_per_s = 3
    outcomes = []
    for _ in range(5):
        res = await env.dispatcher.dispatch("mouse_move",
                                            {"x": 1, "y": 1})
        outcomes.append(res["ok"])
    assert outcomes.count(False) >= 2
    denied = [r for r in [await env.dispatcher.dispatch(
        "mouse_move", {"x": 1, "y": 1})] if not r["ok"]]
    if denied:
        assert denied[0]["error"]["code"] == "POLICY_DENIED"
