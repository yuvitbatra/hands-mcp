"""Real-driver perception contract. Gated: HANDS_CONTRACT_MACOS=1."""
import os
import sys

import pytest
from PIL import Image, ImageDraw

from hands.driver.base import RawFrame
from hands.types import Region

pytestmark = pytest.mark.skipif(
    os.environ.get("HANDS_CONTRACT_MACOS") != "1"
    or sys.platform != "darwin",
    reason="real macOS driver contract tests are opt-in")


@pytest.fixture
def driver():
    from hands.driver.macos import MacOSDriver
    return MacOSDriver()


def test_ocr_reads_rendered_text(driver):
    img = Image.new("RGB", (800, 200), (255, 255, 255))
    ImageDraw.Draw(img).text((40, 60), "HELLO HANDS", fill=(0, 0, 0),
                             font_size=64)
    frame = RawFrame(img, Region(0, 0, 400, 100), 2.0, 1)
    boxes = driver.ocr(frame, ["en-US"])
    assert any("HELLO" in b.text.upper() for b in boxes)
    for b in boxes:
        assert 0.0 <= b.nx <= 1.0 and 0.0 <= b.ny <= 1.0
        assert 0.0 < b.confidence <= 1.0


def test_capture_still_returns_sane_frame(driver):
    frame = driver.capture(None, None)
    assert frame.px_per_pt >= 1.0
    assert frame.image.size[0] > 0
