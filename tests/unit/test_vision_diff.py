from PIL import Image, ImageDraw

from hands.services.vision import annotate, crop, frame_diff
from hands.types import Region


def _img(color, size=(200, 100)):
    return Image.new("RGB", size, color)


def test_identical_frames_no_change():
    d = frame_diff(_img((255, 255, 255)), _img((255, 255, 255)))
    assert d.changed_fraction == 0.0
    assert d.changed_region is None


def test_left_half_changed():
    a = _img((255, 255, 255))
    b = _img((255, 255, 255))
    ImageDraw.Draw(b).rectangle([0, 0, 99, 99], fill=(0, 0, 0))
    d = frame_diff(a, b)
    assert 0.4 < d.changed_fraction < 0.6
    assert d.changed_region.x == 0
    assert d.changed_region.width <= 101


def test_mismatched_sizes_is_full_change():
    d = frame_diff(_img((0, 0, 0), (10, 10)), _img((0, 0, 0), (20, 20)))
    assert d.changed_fraction == 1.0
    assert d.changed_region == Region(0, 0, 20, 20)


def test_small_delta_below_threshold_ignored():
    d = frame_diff(_img((100, 100, 100)), _img((110, 110, 110)))
    assert d.changed_fraction == 0.0


def test_crop_dimensions():
    out = crop(_img((10, 20, 30)), Region(10, 5, 50, 40))
    assert out.size == (50, 40)


def test_annotate_draws_on_a_copy():
    base = _img((255, 255, 255))
    out = annotate(base, [Region(10, 10, 50, 20)])
    assert out is not base
    assert out.getpixel((10, 10)) != (255, 255, 255)
    assert base.getpixel((10, 10)) == (255, 255, 255)
