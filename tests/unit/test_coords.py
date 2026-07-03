import pytest

from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.types import DisplayInfo, Point, Region


@pytest.fixture
def mapper() -> CoordinateMapper:
    return CoordinateMapper(
        [DisplayInfo(1, Region(0, 0, 1440, 900), 2.0, True)])


def test_display_for_inside(mapper):
    assert mapper.display_for(Point(0, 0)).display_id == 1
    assert mapper.display_for(Point(1439.9, 899.9)).display_id == 1


def test_display_for_outside_raises(mapper):
    with pytest.raises(InvalidArgsError):
        mapper.display_for(Point(1440, 900))
    with pytest.raises(InvalidArgsError):
        mapper.display_for(Point(-1, 5))


def test_clamp(mapper):
    assert mapper.clamp(Point(-50, 450)) == Point(0, 450)
    assert mapper.clamp(Point(2000, 2000)) == Point(1439, 899)
    assert mapper.clamp(Point(10, 20)) == Point(10, 20)


def test_screenshot_px_to_pt_full_frame(mapper):
    # 2880x1800 px frame of the whole 1440x900 pt display: px_per_pt = 2
    pt = mapper.screenshot_px_to_pt(Point(2880, 1800),
                                    bounds_pt=Region(0, 0, 1440, 900),
                                    px_per_pt=2.0)
    assert pt == Point(1440, 900)


def test_screenshot_px_to_pt_downscaled_region(mapper):
    # A region starting at (100, 200) pt captured at 0.5 px per pt
    pt = mapper.screenshot_px_to_pt(Point(50, 10),
                                    bounds_pt=Region(100, 200, 400, 300),
                                    px_per_pt=0.5)
    assert pt == Point(200, 220)


def test_vision_normalized_flips_y(mapper):
    # Full-display frame (main display is 1440x900 in the mapper fixture).
    r = mapper.vision_normalized_to_pt(0.25, 0.10, 0.50, 0.20,
                                       Region(0, 0, 1440, 900))
    assert r.x == pytest.approx(360)
    assert r.width == pytest.approx(720)
    assert r.height == pytest.approx(180)
    # bottom edge at 0.10 * 900 from the bottom -> top edge at
    # (1 - 0.10 - 0.20) * 900 = 630 in y-down points.
    assert r.y == pytest.approx(630)


def test_vision_normalized_respects_frame_offset(mapper):
    r = mapper.vision_normalized_to_pt(0.0, 0.0, 1.0, 1.0,
                                       Region(100, 50, 200, 100))
    assert (r.x, r.y, r.width, r.height) == (100, 50, 200, 100)
