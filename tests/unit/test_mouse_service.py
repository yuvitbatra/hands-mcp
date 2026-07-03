import pytest

from hands.config import HandsConfig
from hands.errors import DriverError, InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.mouse import MouseService
from hands.state import StateManager
from hands.types import MouseButton, Point

pytestmark = pytest.mark.anyio


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    cfg.mouse.click_delay_ms = 0  # keep tests fast
    state = StateManager(cfg)
    mapper = CoordinateMapper(fake_driver.displays())
    return MouseService(fake_driver, mapper, state, cfg), fake_driver, state


async def test_move_posts_event_and_updates_state(svc):
    service, driver, state = svc
    got = await service.move(Point(100, 200))
    assert got == Point(100, 200)
    (kind, ev), = driver.pop_events()
    assert (kind, ev.kind, ev.at) == ("mouse", "move", Point(100, 200))
    assert state.cursor == Point(100, 200)


async def test_move_out_of_bounds_rejected_without_side_effect(svc):
    service, driver, _ = svc
    with pytest.raises(InvalidArgsError) as ei:
        await service.move(Point(99999, 5))
    assert not ei.value.details.get("side_effect")
    assert driver.pop_events() == []


async def test_move_clamp(svc):
    service, _, _ = svc
    assert await service.move(Point(99999, 5), clamp=True) == Point(1439, 5)


async def test_click_sequence_is_move_down_up(svc):
    service, driver, _ = svc
    result = await service.click(Point(10, 10))
    assert result.cursor == Point(10, 10)
    kinds = [ev.kind for _, ev in driver.pop_events()]
    assert kinds == ["move", "down", "up"]


async def test_double_click_sets_click_count(svc):
    service, driver, _ = svc
    await service.click(Point(10, 10), count=2)
    events = [ev for _, ev in driver.pop_events()]
    downs = [e for e in events if e.kind == "down"]
    assert [d.click_count for d in downs] == [1, 2]


async def test_right_click_button(svc):
    service, driver, _ = svc
    await service.click(Point(10, 10), button=MouseButton.RIGHT)
    events = [ev for _, ev in driver.pop_events()]
    assert all(e.button is MouseButton.RIGHT for e in events)


async def test_drag_interpolates_and_ends_with_up(svc):
    service, driver, _ = svc
    await service.drag([Point(0, 0), Point(100, 100)], duration_ms=0)
    events = [ev for _, ev in driver.pop_events()]
    assert events[0].kind == "move"          # position at start
    assert events[1].kind == "down"
    assert events[-1].kind == "up"
    moves_during = [e for e in events[2:-1] if e.kind == "move"]
    assert len(moves_during) >= 20           # config.mouse.drag_steps
    assert events[-1].at == Point(100, 100)


async def test_drag_failure_still_releases_button(svc):
    service, driver, _ = svc

    async def run():
        await service.drag([Point(0, 0), Point(100, 100)], duration_ms=0)

    # Fail one of the interpolated moves mid-drag.
    driver.post_mouse_call_count = 0
    original = driver.post_mouse

    def flaky(event):
        driver.post_mouse_call_count += 1
        if driver.post_mouse_call_count == 5:
            raise DriverError("flake")
        original(event)

    driver.post_mouse = flaky
    with pytest.raises(DriverError) as ei:
        await run()
    assert ei.value.details["side_effect"] is True
    events = [ev for _, ev in driver.pop_events()]
    assert events[-1].kind == "up"           # phantom drag prevented


async def test_scroll_positions_then_scrolls(svc):
    service, driver, _ = svc
    await service.scroll(Point(50, 50), dx=0, dy=-3)
    events = driver.pop_events()
    assert events[0][1].kind == "move"
    assert events[1] == ("scroll", Point(50, 50), 0, -3, False)
