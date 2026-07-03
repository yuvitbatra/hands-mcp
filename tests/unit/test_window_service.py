import pytest

from hands.errors import TargetNotFoundError
from hands.services.windows import WindowService
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    return WindowService(fake_driver)


def _seed(drv):
    a = drv.add_window("TextEdit", "com.apple.TextEdit", 42, "Notes.txt",
                       Region(0, 0, 800, 600), focused=True)
    b = drv.add_window("Safari", "com.apple.Safari", 50, "Apple",
                       Region(100, 100, 1200, 700))
    return a, b


async def test_list_filters_by_app(fake_driver, service):
    _seed(fake_driver)
    assert len(await service.list()) == 2
    (w,) = await service.list(app="safari")
    assert w.app_name == "Safari"
    (w2,) = await service.list(app="com.apple.TextEdit")
    assert w2.pid == 42


async def test_focus_by_app_and_title(fake_driver, service):
    _seed(fake_driver)
    win = await service.focus(app="Safari", title_match="Apple")
    assert win.focused


async def test_manage_move_returns_updated_info(fake_driver, service):
    a, _ = _seed(fake_driver)
    win = await service.manage(a, "move", Region(5, 5, 800, 600))
    assert win.bounds == Region(5, 5, 800, 600)


async def test_stale_ref_reresolves_by_fuzzy_title(fake_driver, service):
    a, _ = _seed(fake_driver)
    # Service must have seen the window once to snapshot it.
    await service.list()
    # Simulate the window being replaced: same pid, slightly new title.
    fake_driver.window_perform(a, "close", None)
    fake_driver.add_window("TextEdit", "com.apple.TextEdit", 42,
                           "Notes.txt — Edited", Region(0, 0, 800, 600))
    win = await service.focus(window_ref=a)
    assert win.title == "Notes.txt — Edited" and win.focused


async def test_stale_ref_rejects_dissimilar_title(fake_driver, service):
    a, _ = _seed(fake_driver)
    # Service must have seen the window once to snapshot it.
    await service.list()
    # Simulate the window being replaced: same pid, but a genuinely
    # different title (not a suffix/prefix variant) -> fuzzy match must
    # fail and the stale ref must not silently resolve to the wrong window.
    fake_driver.window_perform(a, "close", None)
    fake_driver.add_window("TextEdit", "com.apple.TextEdit", 42,
                           "Untitled Preferences Panel",
                           Region(0, 0, 800, 600))
    with pytest.raises(TargetNotFoundError) as ei:
        await service.focus(window_ref=a)
    assert ei.value.details["candidates"]


async def test_unresolvable_ref_lists_candidates(fake_driver, service):
    _seed(fake_driver)
    await service.list()
    with pytest.raises(TargetNotFoundError) as ei:
        await service.focus(window_ref="42:999")
    assert ei.value.details["candidates"]
