from types import SimpleNamespace

import pytest

from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.windows import WindowService
from hands.tools import windows as window_tools
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    driver = FakeDriver()
    windows = WindowService(driver)
    container = SimpleNamespace(windows=windows)
    reg = ToolRegistry()
    window_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg, windows=windows)


def _seed(drv):
    a = drv.add_window("TextEdit", "com.apple.TextEdit", 42, "Notes.txt",
                       Region(0, 0, 800, 600), focused=True)
    b = drv.add_window("Safari", "com.apple.Safari", 50, "Apple",
                       Region(100, 100, 1200, 700))
    return a, b


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_window_list_returns_seeded_windows(env):
    _seed(env.driver)
    res = await _call(env, "window_list", {})
    assert len(res["windows"]) == 2
    names = {w["app_name"] for w in res["windows"]}
    assert names == {"TextEdit", "Safari"}


async def test_window_list_policy_class_is_read(env):
    spec = env.registry.get("window_list")
    assert spec.policy_class == "read"


async def test_window_focus_focuses(env):
    _seed(env.driver)
    res = await _call(env, "window_focus", {"app": "Safari"})
    assert res["window"]["app_name"] == "Safari"
    assert res["window"]["focused"] is True


async def test_window_focus_policy_class_is_act(env):
    spec = env.registry.get("window_focus")
    assert spec.policy_class == "act"


async def test_window_manage_moves(env):
    a, _ = _seed(env.driver)
    res = await _call(env, "window_manage", {
        "window_ref": a, "action": "move",
        "bounds": {"x": 5, "y": 5, "width": 800, "height": 600}})
    assert res["window"]["bounds"] == {"x": 5.0, "y": 5.0,
                                       "width": 800.0, "height": 600.0}


async def test_window_manage_policy_class_is_act(env):
    spec = env.registry.get("window_manage")
    assert spec.policy_class == "act"


async def test_window_manage_escalates_only_for_close(env):
    spec = env.registry.get("window_manage")
    move_args = spec.args_model.model_validate({
        "window_ref": "1:1", "action": "move",
        "bounds": {"x": 0, "y": 0, "width": 10, "height": 10}})
    close_args = spec.args_model.model_validate({
        "window_ref": "1:1", "action": "close"})
    assert spec.escalate(move_args) is False
    assert spec.escalate(close_args) is True
