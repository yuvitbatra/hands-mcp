from types import SimpleNamespace

import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.verification import VerificationEngine
from hands.services.waiter import Waiter
from hands.state import StateManager
from hands.tools import register_builtin_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    driver = FakeDriver()
    state = StateManager(cfg)
    coords = CoordinateMapper(driver.displays())
    shots = ScreenshotService(driver, state, cfg)
    ocr = OCRService(driver, coords, cfg)
    container = SimpleNamespace(config=cfg, driver=driver, state=state,
                                coords=coords, screenshots=shots, ocr=ocr,
                                waiter=Waiter(shots, ocr, cfg),
                                verification=VerificationEngine(
                                    shots, ocr, driver, cfg),
                                mouse=None, keyboard=None, clipboard=None)
    reg = ToolRegistry()
    register_builtin_tools(reg, container)
    return SimpleNamespace(driver=driver, registry=reg)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_find_text_returns_clickable_center(env):
    env.driver.set_ocr_boxes(
        [RawTextBox("Submit", 0.0, 0.0, 0.5, 0.5, 0.97)])
    res = await _call(env, "find_text", {"text": "submit"})
    (m,) = res["matches"]
    assert m["text"] == "Submit"
    assert m["center"] == {"x": 360.0, "y": 675.0}
    assert m["confidence"] == 0.97


async def test_find_text_fuzzy_and_exact(env):
    env.driver.set_ocr_boxes(
        [RawTextBox("Submlt", 0.0, 0.0, 0.5, 0.5, 0.8)])  # OCR typo
    fuzzy = await _call(env, "find_text", {"text": "Submit"})
    assert len(fuzzy["matches"]) == 1
    exact = await _call(env, "find_text",
                        {"text": "Submit", "fuzzy": False})
    assert exact["matches"] == []


async def test_find_text_no_match_is_ok_empty(env):
    env.driver.set_ocr_boxes([])
    res = await _call(env, "find_text", {"text": "anything"})
    assert res["matches"] == []


async def test_wait_tool_condition(env):
    env.driver.set_ocr_boxes([RawTextBox("Ready", 0, 0, 0.2, 0.1, 1.0)])
    res = await _call(env, "wait", {
        "condition": {"type": "text_present", "text": "Ready"},
        "timeout_ms": 500})
    assert res["met"] is True


async def test_wait_tool_duration_back_compat(env):
    res = await _call(env, "wait", {"duration_ms": 5})
    assert res["met"] is True


async def test_wait_tool_requires_exactly_one_form(env):
    from pydantic import ValidationError
    spec = env.registry.get("wait")
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({})
    with pytest.raises(ValidationError):
        spec.args_model.model_validate(
            {"duration_ms": 5, "condition": {"type": "duration", "ms": 1}})


async def test_verify_tool_text_present(env):
    env.driver.set_ocr_boxes([RawTextBox("Done", 0, 0, 0.2, 0.1, 0.95)])
    res = await _call(env, "verify",
                      {"expect": {"type": "text_present", "text": "Done"}})
    assert res["passed"] is True
    assert res["confidence"] == 0.95
