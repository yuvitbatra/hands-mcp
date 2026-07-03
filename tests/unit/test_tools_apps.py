from types import SimpleNamespace

import pytest

from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.apps import AppService
from hands.tools import apps as app_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    driver = FakeDriver()
    apps = AppService(driver, waiter=None)
    container = SimpleNamespace(apps=apps)
    reg = ToolRegistry()
    app_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg, apps=apps)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_app_open_and_list(env):
    env.driver.install_app("Notes", "com.apple.Notes")
    res = await _call(env, "app_open",
                      {"app": "Notes", "wait_for_window": False})
    assert res["app"]["name"] == "Notes"
    listing = await _call(env, "app_list", {})
    assert listing["frontmost"]["name"] == "Notes"


async def test_app_close_force_escalates(env):
    spec = env.registry.get("app_close")
    assert spec.escalate(spec.args_model.model_validate(
        {"app": "Notes", "force": True})) is True
    assert spec.escalate(spec.args_model.model_validate(
        {"app": "Notes"})) is False
