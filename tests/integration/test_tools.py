import base64
from types import SimpleNamespace

import pytest

from hands.audit import AuditLogger
from hands.config import HandsConfig
from hands.dispatcher import Dispatcher
from hands.metrics import Metrics
from hands.permissions import AllowAllPermissions
from hands.registry import ToolRegistry
from hands.services.clipboard import ClipboardService
from hands.services.coords import CoordinateMapper
from hands.services.keyboard import KeyboardService
from hands.services.mouse import MouseService
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.verification import VerificationEngine
from hands.services.waiter import Waiter
from hands.services.windows import WindowService
from hands.state import StateManager
from hands.tools import register_builtin_tools

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"screenshot", "get_state", "wait", "mouse_move",
                  "mouse_click", "mouse_drag", "mouse_scroll",
                  "keyboard_type", "key_press", "find_text", "verify",
                  "clipboard_get", "clipboard_set", "clipboard_paste",
                  "window_list", "window_focus", "window_manage"}


@pytest.fixture
def wired(fake_driver, tmp_path):
    cfg = HandsConfig()
    cfg.mouse.click_delay_ms = 0
    cfg.keyboard.chunk_delay_ms = 0
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    state = StateManager(cfg)
    coords = CoordinateMapper(fake_driver.displays())
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, coords, cfg)
    keyboard = KeyboardService(fake_driver, cfg)
    container = SimpleNamespace(
        config=cfg, driver=fake_driver, state=state,
        mouse=MouseService(fake_driver, coords, state, cfg),
        keyboard=keyboard,
        clipboard=ClipboardService(fake_driver, keyboard, cfg),
        windows=WindowService(fake_driver),
        screenshots=shots, ocr=ocr,
        waiter=Waiter(shots, ocr, cfg),
        verification=VerificationEngine(shots, ocr, fake_driver, cfg))
    reg = ToolRegistry()
    register_builtin_tools(reg, container)
    disp = Dispatcher(reg, AllowAllPermissions(), state,
                      AuditLogger(cfg), Metrics(), cfg)
    return disp, reg, fake_driver


def test_all_builtins_registered(wired):
    _, reg, _ = wired
    assert {s.name for s in reg.list_specs()} == EXPECTED_TOOLS


async def test_screenshot_tool_returns_png_and_meta(wired):
    disp, _, _ = wired
    res = await disp.dispatch("screenshot", {})
    assert res["ok"] is True
    assert base64.b64decode(res["image_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert res["bounds_pt"]["width"] == 1440
    assert res["px_per_pt"] > 0


async def test_click_tool_moves_and_reports_cursor(wired):
    disp, _, driver = wired
    res = await disp.dispatch("mouse_click", {"x": 10, "y": 20})
    assert res["ok"] is True
    assert res["cursor"] == {"x": 10, "y": 20}
    kinds = [ev.kind for _, ev in driver.pop_events()]
    assert kinds == ["move", "down", "up"]


async def test_keyboard_type_tool(wired):
    disp, _, driver = wired
    res = await disp.dispatch("keyboard_type", {"text": "hi there"})
    assert res["ok"] is True and res["chars_typed"] == 8
    assert driver.typed_text() == "hi there"


async def test_key_press_bad_chord_is_invalid_args(wired):
    disp, _, _ = wired
    res = await disp.dispatch("key_press", {"chord": "cmd+Retrun"})
    assert res["error"]["code"] == "INVALID_ARGS"


async def test_get_state_reports_cursor_and_displays(wired):
    disp, _, _ = wired
    await disp.dispatch("mouse_move", {"x": 7, "y": 9})
    res = await disp.dispatch("get_state", {"include_history": 5})
    assert res["cursor"] == {"x": 7, "y": 9}
    assert res["displays"][0]["bounds_pt"]["width"] == 1440
    assert res["history"][-1]["tool"] == "mouse_move"


async def test_wait_tool(wired):
    disp, _, _ = wired
    res = await disp.dispatch("wait", {"duration_ms": 10})
    assert res["ok"] is True and res["met"] is True
