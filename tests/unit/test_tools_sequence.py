"""Unit tests for execute_sequence (M4 Task 2)."""
import pytest

from hands.config import HandsConfig
from hands.container import Container
from hands.permissions import Allowed, Denied

pytestmark = pytest.mark.anyio


@pytest.fixture
def container(tmp_path):
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    return Container.build(cfg)


async def test_sequence_executes_steps_in_order(container):
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 10, "y": 10}},
            {"tool": "keyboard_type", "args": {"text": "hi"}},
        ]})
    assert res["ok"] is True
    assert res["completed"] == 2
    assert all(r["ok"] for r in res["results"])
    kinds = [e[0] for e in container.driver.pop_events()]
    assert kinds.index("mouse") < kinds.index("type")
    assert "image_b64" in res            # at top level for MCP ImageContent
    assert "image_b64" not in res.get("final_screenshot", {})  # meta only


async def test_guard_failure_halts_with_evidence(container):
    container.driver.set_ocr_boxes([])       # guard text never appears
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 1, "y": 1}},
            {"tool": "mouse_move", "args": {"x": 2, "y": 2},
             "guard": {"type": "text_present", "text": "Ready"},
             "guard_timeout_ms": 30},
        ],
        "screenshot_after": False})
    assert res["ok"] is True                # sequence answered, not errored
    assert res["completed"] == 1
    assert res["halted_by"] == "guard"
    assert res["results"][1] == {"skipped": True}


async def test_step_failure_halts_when_stop_on_failure(container):
    # x=99_999 is outside the 1440x900 fake display → InvalidArgsError
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 99_999, "y": 5}},
            {"tool": "mouse_move", "args": {"x": 2, "y": 2}},
        ],
        "screenshot_after": False})
    assert res["ok"] is True               # sequence answered, not errored
    assert res["completed"] == 0
    assert res["halted_by"] == "failure"
    assert res["results"][0]["ok"] is False
    assert res["results"][0]["error"]["code"] == "INVALID_ARGS"
    assert res["results"][1] == {"skipped": True}


async def test_rejects_read_tools_nesting_and_unknown(container):
    for steps in (
        [{"tool": "screenshot"}],                             # read tool
        [{"tool": "execute_sequence", "args": {"steps": [
            {"tool": "mouse_move", "args": {"x": 1, "y": 1}}
        ]}}],                                                  # nested sequence
        [{"tool": "no_such_tool"}],                            # unknown
    ):
        res = await container.dispatcher.dispatch(
            "execute_sequence", {"steps": steps})
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_ARGS"


async def test_any_denied_step_rejects_whole_sequence(container):
    class DenyTyping:
        def authorize(self, action):
            if action.tool.startswith("keyboard"):
                return Denied("no typing")
            return Allowed()

        async def confirm(self, prompt, action):
            return True

    deny = DenyTyping()
    container.dispatcher.permissions = deny

    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 1, "y": 1}},
            {"tool": "keyboard_type", "args": {"text": "hi"}},
        ]})
    assert res["ok"] is False
    assert res["error"]["code"] == "POLICY_DENIED"
    assert container.driver.pop_events() == []      # nothing ran


async def test_sequence_respects_deny_listed_frontmost_app(container):
    """Regression: execute_sequence must feed target_app into each step's
    ActionDescriptor so the real PermissionEngine's app deny-list (DESIGN
    §13.3) applies inside a sequence too, not just via direct dispatch()."""
    container.driver.install_app("System Preferences",
                                 "com.apple.systempreferences")
    container.driver.launch_app("com.apple.systempreferences")
    container.driver.pop_events()

    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [{"tool": "mouse_move", "args": {"x": 1, "y": 1}}]})

    assert res["ok"] is False
    assert res["error"]["code"] == "POLICY_DENIED"
    assert container.driver.pop_events() == []      # nothing ran


async def test_sequence_continues_past_failure_when_stop_on_failure_false(
        container):
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 99_999, "y": 5}},
            {"tool": "mouse_move", "args": {"x": 2, "y": 2}},
        ],
        "stop_on_failure": False,
        "screenshot_after": False})
    assert res["ok"] is True
    assert res["completed"] == 1          # second step succeeded
    assert "halted_by" not in res         # no halt
    assert res["results"][0]["ok"] is False
    assert res["results"][1]["ok"] is True
