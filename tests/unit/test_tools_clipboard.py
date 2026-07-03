import base64
from types import SimpleNamespace

import pytest

from hands.config import HandsConfig
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.clipboard import ClipboardService
from hands.services.keyboard import KeyboardService
from hands.tools import clipboard as clipboard_tools
from hands.types import ClipboardContent

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    cfg.clipboard.restore_delay_ms = 0
    driver = FakeDriver()
    keyboard = KeyboardService(driver, cfg)
    clip = ClipboardService(driver, keyboard, cfg)
    container = SimpleNamespace(config=cfg, clipboard=clip)
    reg = ToolRegistry()
    clipboard_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg, clip=clip)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_clipboard_get_is_sensitive_read(env):
    spec = env.registry.get("clipboard_get")
    assert spec.policy_class == "sensitive"
    assert spec.idempotent is True


async def test_set_then_get_text(env):
    await _call(env, "clipboard_set", {"text": "abc"})
    res = await _call(env, "clipboard_get", {})
    assert res["kind"] == "text" and res["text"] == "abc"


async def test_set_image_b64(env):
    png = base64.b64encode(b"\x89PNG fake").decode()
    await _call(env, "clipboard_set", {"image_b64": png})
    res = await _call(env, "clipboard_get", {"format": "image"})
    assert res["kind"] == "image"
    assert base64.b64decode(res["image_b64"]) == b"\x89PNG fake"


async def test_set_requires_exactly_one_payload(env):
    from pydantic import ValidationError
    spec = env.registry.get("clipboard_set")
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({})
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({"text": "a", "image_b64": "Yg=="})


async def test_paste_tool(env):
    await _call(env, "clipboard_set", {"text": "keep me"})
    res = await _call(env, "clipboard_paste", {"text": "insert this"})
    assert res == {}
    assert (await env.clip.get()).text == "keep me"
