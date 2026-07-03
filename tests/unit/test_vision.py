from PIL import Image

from hands.driver.base import RawFrame
from hands.services.vision import downscale, encode, perceptual_hash
from hands.types import Region


def _frame(w_px: int, h_px: int, px_per_pt: float = 2.0) -> RawFrame:
    return RawFrame(Image.new("RGB", (w_px, h_px), (200, 10, 10)),
                    Region(0, 0, w_px / px_per_pt, h_px / px_per_pt),
                    px_per_pt, 1)


def test_downscale_caps_long_edge_and_rescales_ppp():
    img, ppp = downscale(_frame(2880, 1800), max_dim=1440)
    assert img.size == (1440, 900)
    assert ppp == 1.0  # 1440 px over 1440 pt


def test_downscale_noop_when_small_enough():
    img, ppp = downscale(_frame(800, 600), max_dim=1568)
    assert img.size == (800, 600)
    assert ppp == 2.0


def test_encode_png_and_jpeg_magic_bytes():
    img = Image.new("RGB", (10, 10))
    assert encode(img, "png", 80)[:8] == b"\x89PNG\r\n\x1a\n"
    assert encode(img, "jpeg", 80)[:2] == b"\xff\xd8"


def test_phash_stable_and_content_sensitive():
    a = Image.new("RGB", (64, 64), (0, 0, 0))
    b = Image.new("RGB", (64, 64), (0, 0, 0))
    half = Image.new("RGB", (64, 64), (0, 0, 0))
    half.paste((255, 255, 255), (0, 0, 64, 32))
    assert perceptual_hash(a) == perceptual_hash(b)
    assert perceptual_hash(a) != perceptual_hash(half)
    assert len(perceptual_hash(a)) == 16
