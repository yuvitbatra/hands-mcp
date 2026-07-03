import anyio
import pytest
from pydantic import BaseModel

from hands.audit import AuditLogger
from hands.config import HandsConfig
from hands.dispatcher import Dispatcher
from hands.errors import DriverError
from hands.metrics import Metrics
from hands.permissions import Allowed, AllowAllPermissions, Denied
from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy
from hands.state import StateManager

pytestmark = pytest.mark.anyio


class NoArgs(BaseModel, extra="forbid"):
    pass


class FreshArgs(BaseModel, extra="forbid"):
    require_fresh_screenshot: bool | None = None


class DenyAll:
    def authorize(self, action):
        return Denied("policy says no")


def make(tmp_path, specs, permissions=None):
    cfg = HandsConfig()
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    reg = ToolRegistry()
    for s in specs:
        reg.register(s)
    state = StateManager(cfg)
    disp = Dispatcher(reg, permissions or AllowAllPermissions(), state,
                      AuditLogger(cfg), Metrics(), cfg)
    return disp, state, cfg


def spec(name, handler, *, policy="act", retry=None, model=NoArgs):
    return ToolSpec(name=name, description="d", args_model=model,
                    handler=handler, policy_class=policy,
                    retry=retry or RetryPolicy.none())


async def ok_handler(args, ctx):
    return {"answer": 42}


async def test_happy_path_envelope(tmp_path):
    disp, state, _ = make(tmp_path, [spec("t", ok_handler)])
    res = await disp.dispatch("t", {})
    assert res["ok"] is True and res["answer"] == 42
    assert "request_id" in res
    assert state.history(1)[0].outcome == "ok"


async def test_unknown_tool(tmp_path):
    disp, _, _ = make(tmp_path, [])
    res = await disp.dispatch("nope", {})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_ARGS"


async def test_extra_field_rejected(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler)])
    res = await disp.dispatch("t", {"surprise": 1})
    assert res["error"]["code"] == "INVALID_ARGS"
    assert res["error"]["retryable"] is False


async def test_kill_switch_blocks(tmp_path):
    disp, _, cfg = make(tmp_path, [spec("t", ok_handler)])
    cfg.security.kill_switch_path.touch()
    res = await disp.dispatch("t", {})
    assert res["error"]["code"] == "KILL_SWITCH"


async def test_policy_denial(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler)],
                      permissions=DenyAll())
    res = await disp.dispatch("t", {})
    assert res["error"]["code"] == "POLICY_DENIED"


async def test_transient_failure_retried_for_read_tools(tmp_path):
    calls = []

    async def flaky(args, ctx):
        calls.append(1)
        if len(calls) < 2:
            raise DriverError("flake")
        return {}

    disp, _, _ = make(tmp_path, [spec("t", flaky, policy="read",
                                      retry=RetryPolicy.read())])
    res = await disp.dispatch("t", {})
    assert res["ok"] is True and len(calls) == 2


async def test_act_marks_screen_dirty_read_does_not(tmp_path):
    disp, state, _ = make(tmp_path, [spec("a", ok_handler, policy="act"),
                                     spec("r", ok_handler, policy="read")])
    state.clear_screen_dirty()
    await disp.dispatch("r", {})
    assert state.screen_dirty is False
    await disp.dispatch("a", {})
    assert state.screen_dirty is True


async def test_act_tools_serialize_on_the_action_lock(tmp_path):
    log = []

    async def slow(args, ctx):
        log.append("enter")
        await anyio.sleep(0.02)
        log.append("exit")
        return {}

    disp, _, _ = make(tmp_path, [spec("slow", slow)])
    async with anyio.create_task_group() as tg:
        tg.start_soon(disp.dispatch, "slow", {})
        tg.start_soon(disp.dispatch, "slow", {})
    assert log == ["enter", "exit", "enter", "exit"]


async def test_staleness_gate(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler, model=FreshArgs)])
    res = await disp.dispatch("t", {"require_fresh_screenshot": True})
    assert res["error"]["code"] == "STALE_SCREENSHOT"
    assert res["error"]["retryable"] is True


async def test_audit_line_written(tmp_path):
    disp, _, cfg = make(tmp_path, [spec("t", ok_handler)])
    await disp.dispatch("t", {})
    lines = cfg.security.audit_path.read_text().strip().splitlines()
    assert len(lines) == 1 and '"t"' in lines[0]


async def test_escalate_marks_call_sensitive(tmp_path):
    """ToolSpec.escalate upgrades policy_class for matching args (M3)."""

    seen: list = []

    class Recorder:
        def authorize(self, action):
            seen.append(action.policy_class)
            return Allowed()

    class Args(BaseModel, extra="forbid"):
        force: bool = False

    async def handler(args, ctx):
        return {}

    demo_spec = ToolSpec(
        name="demo_close", description="d", args_model=Args,
        handler=handler, policy_class="act", retry=RetryPolicy.none(),
        idempotent=False, escalate=lambda a: a.force)
    disp, _, _ = make(tmp_path, [demo_spec], permissions=Recorder())
    await disp.dispatch("demo_close", {"force": False})
    await disp.dispatch("demo_close", {"force": True})
    assert seen == ["act", "sensitive"]
