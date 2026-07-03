"""Same assertions against fake and (opt-in) real driver (DESIGN §12).

The real-driver leg is READ-ONLY: it captures and reads, never posts
events. Opt in on macOS (needs Screen Recording permission) with:
    HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q
"""
import os
import sys

import pytest

from hands.driver.base import Driver
from hands.driver.fake import FakeDriver


def _params() -> list[str]:
    params = ["fake"]
    if (sys.platform == "darwin"
            and os.environ.get("HANDS_CONTRACT_MACOS") == "1"):
        params.append("macos")
    return params


@pytest.fixture(params=_params())
def driver(request) -> Driver:
    if request.param == "fake":
        return FakeDriver()
    from hands.driver.macos import MacOSDriver
    return MacOSDriver()


def test_satisfies_protocol(driver):
    assert isinstance(driver, Driver)


def test_exactly_one_main_display(driver):
    mains = [d for d in driver.displays() if d.is_main]
    assert len(mains) == 1
    assert mains[0].scale >= 1.0
    assert mains[0].bounds_pt.width > 0


def test_full_capture_geometry(driver):
    d = next(x for x in driver.displays() if x.is_main)
    frame = driver.capture(None, None)
    assert frame.bounds_pt == d.bounds_pt
    # encoded pixel width must match bounds * px_per_pt (±2 px rounding)
    assert abs(frame.image.width
               - frame.bounds_pt.width * frame.px_per_pt) <= 2


def test_region_capture_geometry(driver):
    from hands.types import Region
    region = Region(10, 10, 200, 100)
    frame = driver.capture(region, None)
    assert frame.bounds_pt == region
    assert abs(frame.image.width - 200 * frame.px_per_pt) <= 2


def test_cursor_position_within_a_display(driver):
    p = driver.cursor_position()
    assert any(d.bounds_pt.contains(p) for d in driver.displays())
