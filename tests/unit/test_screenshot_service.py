import pytest

from hands.config import HandsConfig
from hands.errors import TargetNotFoundError
from hands.services.screenshot import ScreenshotService
from hands.state import StateManager
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    return ScreenshotService(fake_driver, StateManager(cfg), cfg), fake_driver


async def test_capture_returns_metadata_and_png(svc):
    service, _ = svc
    shot = await service.capture()
    assert shot.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot.px_per_pt <= 2.0            # downscaled to max_dim
    assert shot.bounds_pt == Region(0, 0, 1440, 900)
    assert shot.cached is False
    assert len(shot.phash) == 16


async def test_capture_downscales_to_max_dim(svc):
    service, _ = svc
    shot = await service.capture(max_dim=1440)
    # 2880 px long edge -> 1440: px_per_pt drops from 2.0 to 1.0
    assert shot.px_per_pt == 1.0


async def test_second_capture_hits_cache(svc):
    service, driver = svc
    first = await service.capture()
    second = await service.capture()
    assert second.cached is True
    assert second.screenshot_id == first.screenshot_id
    # only one real driver capture happened
    assert len([e for e in driver.pop_events()]) == 0  # capture isn't an event
    assert first.data == second.data


async def test_dirty_screen_busts_cache(svc):
    service, _ = svc
    first = await service.capture()
    service._state.mark_screen_dirty()
    second = await service.capture()
    assert second.cached is False
    assert second.screenshot_id != first.screenshot_id


async def test_fresh_flag_busts_cache(svc):
    service, _ = svc
    first = await service.capture()
    second = await service.capture(fresh=True)
    assert second.cached is False


async def test_capture_clears_dirty_and_records_meta(svc):
    service, _ = svc
    state = service._state
    assert state.screen_dirty is True
    shot = await service.capture()
    assert state.screen_dirty is False
    assert state.latest_screenshot_meta["screenshot_id"] == shot.screenshot_id


async def test_get_unknown_id_raises(svc):
    service, _ = svc
    with pytest.raises(TargetNotFoundError):
        service.get("nope")
