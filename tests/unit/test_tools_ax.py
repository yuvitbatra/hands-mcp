import pytest
from types import SimpleNamespace

from hands.config import HandsConfig
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.apps import AppService
from hands.tools import ax as ax_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    driver = FakeDriver()
    container = SimpleNamespace(config=cfg, driver=driver,
                                apps=AppService(driver, waiter=None))
    reg = ToolRegistry()
    ax_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_get_ui_tree_serializes(env):
    env.driver.install_app("Notes", "com.apple.Notes")
    env.driver.launch_app("Notes")
    res = await _call(env, "get_ui_tree", {"app": "Notes"})
    assert res["tree"]["role"] == "AXApplication"
    assert res["truncated"] is False


async def test_get_ui_tree_node_cap(env):
    from hands.driver.base import AXNode
    kids = tuple(AXNode("AXButton", f"b{i}", None, None)
                 for i in range(600))
    env.driver.set_ax_tree(AXNode("AXApplication", "Big", None, None,
                                  (), kids))
    env.driver.install_app("Big", "com.example.Big")
    env.driver.launch_app("Big")
    res = await _call(env, "get_ui_tree", {"app": "Big"})
    assert res["truncated"] is True
