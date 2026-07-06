import pytest

from hands.errors import TargetNotFoundError
from hands.services.apps import AppService
from hands.config import HandsConfig
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.waiter import Waiter
from hands.state import StateManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    # waiter=None is fine while wait_for_window=False (Task 6 wires it).
    return AppService(fake_driver, waiter=None)


async def test_open_launches_then_activates(fake_driver, service):
    fake_driver.install_app("Notes", "com.apple.Notes")
    app, _ = await service.open("Notes", wait_for_window=False)
    assert app.frontmost
    again, _ = await service.open("Notes", wait_for_window=False)
    assert again.pid == app.pid


async def test_open_unknown_app(fake_driver, service):
    with pytest.raises(TargetNotFoundError):
        await service.open("Ghost", wait_for_window=False)


async def test_close(fake_driver, service):
    fake_driver.install_app("Notes", "com.apple.Notes")
    await service.open("Notes", wait_for_window=False)
    await service.close("Notes")
    assert await service.list_running() == []


async def test_close_not_running(fake_driver, service):
    with pytest.raises(TargetNotFoundError):
        await service.close("Notes")


async def test_open_waits_for_window(fake_driver):
    cfg = HandsConfig()
    cfg.waiter.poll_start_ms = 5
    shots = ScreenshotService(fake_driver, StateManager(cfg), cfg)
    ocr = OCRService(fake_driver,
                     CoordinateMapper(fake_driver.displays()), cfg)
    waiter = Waiter(shots, ocr, cfg, driver=fake_driver)
    svc = AppService(fake_driver, waiter)
    fake_driver.install_app("Notes", "com.apple.Notes")
    app, window = await svc.open("Notes")
    assert window is not None and window.pid == app.pid
