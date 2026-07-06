import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.waiter import Waiter
from hands.state import StateManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def waiter(fake_driver):
    cfg = HandsConfig()
    cfg.waiter.poll_start_ms = 5
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    return Waiter(shots, ocr, cfg)


async def test_duration(waiter):
    res = await waiter.wait_for({"type": "duration", "ms": 10}, 1000)
    assert res.met and res.waited_ms == 10


async def test_text_present_met(fake_driver, waiter):
    fake_driver.set_ocr_boxes([RawTextBox("Done", 0, 0, 0.2, 0.1, 0.9)])
    res = await waiter.wait_for(
        {"type": "text_present", "text": "done"}, 500)
    assert res.met
    assert res.evidence["matches"][0]["text"] == "Done"


async def test_text_present_timeout_is_answer_not_error(waiter):
    res = await waiter.wait_for(
        {"type": "text_present", "text": "never"}, 60)
    assert res.met is False
    assert res.waited_ms >= 60


async def test_screen_stable_on_static_screen(waiter):
    res = await waiter.wait_for(
        {"type": "screen_stable", "quiet_ms": 20}, 2000)
    assert res.met


async def test_unknown_condition(waiter):
    with pytest.raises(InvalidArgsError):
        await waiter.wait_for({"type": "moon_phase"}, 100)


@pytest.fixture
def waiter_with_driver(fake_driver):
    cfg = HandsConfig()
    cfg.waiter.poll_start_ms = 5
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    return Waiter(shots, ocr, cfg, driver=fake_driver), fake_driver


async def test_window_present_and_gone(waiter_with_driver):
    from hands.types import Region
    waiter, fake_driver = waiter_with_driver
    res = await waiter.wait_for(
        {"type": "window_present", "app": "Notes"}, 40)
    assert res.met is False
    fake_driver.add_window("Notes", "com.apple.Notes", 7, "My Note",
                           Region(0, 0, 400, 300))
    res = await waiter.wait_for(
        {"type": "window_present", "app": "Notes", "title": "note"}, 500)
    assert res.met
    res = await waiter.wait_for(
        {"type": "window_gone", "title": "My Note"}, 40)
    assert res.met is False


async def test_app_frontmost(waiter_with_driver):
    waiter, fake_driver = waiter_with_driver
    fake_driver.install_app("Notes", "com.apple.Notes")
    fake_driver.launch_app("Notes")
    res = await waiter.wait_for(
        {"type": "app_frontmost", "app": "com.apple.Notes"}, 500)
    assert res.met
