import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.verification import (
    Expectation,
    VerificationEngine,
)
from hands.state import StateManager
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def env(fake_driver):
    cfg = HandsConfig()
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    engine = VerificationEngine(shots, ocr, fake_driver, cfg)
    return fake_driver, shots, engine


def test_from_wire_parses_composites():
    e = Expectation.from_wire({
        "type": "all_of",
        "children": [{"type": "text_present", "text": "OK"},
                     {"type": "cursor_at", "x": 1, "y": 2}]})
    assert e.type == "all_of"
    assert e.children[0].params == {"text": "OK"}


def test_from_wire_rejects_unknown_type():
    with pytest.raises(InvalidArgsError):
        Expectation.from_wire({"type": "vibes"})


async def test_text_present_and_absent(env):
    driver, _, engine = env
    driver.set_ocr_boxes([RawTextBox("Saved", 0, 0, 0.2, 0.1, 0.9)])
    ok = await engine.verify(
        Expectation.from_wire({"type": "text_present", "text": "Saved"}))
    assert ok.passed and ok.confidence == 0.9
    absent = await engine.verify(
        Expectation.from_wire({"type": "text_absent", "text": "Saved"}))
    assert not absent.passed


async def test_region_changed_against_baseline(env):
    driver, shots, engine = env
    baseline = await shots.capture(fresh=True)
    driver.draw_rect(Region(0, 0, 200, 200), (255, 255, 255))
    changed = await engine.verify(
        Expectation.from_wire({"type": "region_changed",
                               "region": {"x": 0, "y": 0,
                                          "width": 200, "height": 200}}),
        baseline=baseline)
    assert changed.passed
    unchanged = await engine.verify(
        Expectation.from_wire({"type": "region_unchanged",
                               "region": {"x": 1200, "y": 700,
                                          "width": 100, "height": 100}}),
        baseline=baseline)
    assert unchanged.passed


async def test_region_changed_respects_configured_diff_threshold(
        fake_driver):
    cfg = HandsConfig()
    cfg.verification.diff_threshold = 1.5  # impossible to exceed
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    engine = VerificationEngine(shots, ocr, fake_driver, cfg)
    baseline = await shots.capture(fresh=True)
    fake_driver.draw_rect(Region(0, 0, 200, 200), (255, 255, 255))
    changed = await engine.verify(
        Expectation.from_wire({"type": "region_changed",
                               "region": {"x": 0, "y": 0,
                                          "width": 200, "height": 200}}),
        baseline=baseline)
    assert not changed.passed


async def test_region_changed_requires_baseline(env):
    _, _, engine = env
    with pytest.raises(InvalidArgsError):
        await engine.verify(Expectation.from_wire(
            {"type": "region_changed",
             "region": {"x": 0, "y": 0, "width": 10, "height": 10}}))


async def test_cursor_at_with_tolerance(env):
    driver, _, engine = env
    from hands.driver.base import MouseEventSpec
    from hands.types import MouseButton, Point
    driver.post_mouse(MouseEventSpec("move", Point(100, 100),
                                     MouseButton.LEFT))
    res = await engine.verify(Expectation.from_wire(
        {"type": "cursor_at", "x": 101, "y": 99}))
    assert res.passed and res.confidence == 1.0


async def test_all_of_collects_failed_clauses(env):
    driver, _, engine = env
    driver.set_ocr_boxes([RawTextBox("Saved", 0, 0, 0.2, 0.1, 0.9)])
    res = await engine.verify(Expectation.from_wire({
        "type": "all_of",
        "children": [{"type": "text_present", "text": "Saved"},
                     {"type": "text_present", "text": "Missing"}]}))
    assert not res.passed
    assert "text_present" in res.failed_clauses


@pytest.fixture
def env_with_clipboard(fake_driver):
    from hands.services.clipboard import ClipboardService
    from hands.services.keyboard import KeyboardService
    cfg = HandsConfig()
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    keyboard = KeyboardService(fake_driver, cfg)
    clipboard = ClipboardService(fake_driver, keyboard, cfg)
    engine = VerificationEngine(shots, ocr, fake_driver, cfg,
                                clipboard=clipboard)
    return fake_driver, shots, engine


async def test_window_present_strategy(env_with_clipboard):
    driver, _, engine = env_with_clipboard
    from hands.types import Region
    driver.add_window("Notes", "com.apple.Notes", 7, "My Note",
                      Region(0, 0, 400, 300))
    res = await engine.verify(Expectation.from_wire(
        {"type": "window_present", "title": "My Note"}))
    assert res.passed and res.confidence == 1.0
    gone = await engine.verify(Expectation.from_wire(
        {"type": "window_gone", "title": "My Note"}))
    assert not gone.passed


async def test_clipboard_contains_redacts_evidence(env_with_clipboard):
    driver, _, engine = env_with_clipboard
    from hands.types import ClipboardContent
    driver.clipboard_write(ClipboardContent("text", text="secret token"))
    res = await engine.verify(Expectation.from_wire(
        {"type": "clipboard_contains", "text": "token"}))
    assert res.passed
    assert "secret" not in str(res.evidence)
    assert res.evidence["clipboard_len"] == len("secret token")
