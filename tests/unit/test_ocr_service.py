import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    coords = CoordinateMapper(fake_driver.displays())
    return OCRService(fake_driver, coords, HandsConfig())


async def test_recognize_converts_to_canonical_points(fake_driver, service):
    # Box occupying the bottom-left quarter of the 1440x900 screen.
    fake_driver.set_ocr_boxes(
        [RawTextBox("Login", 0.0, 0.0, 0.5, 0.5, 0.9)])
    (box,) = await service.recognize()
    assert box.text == "Login"
    assert box.confidence == 0.9
    # bottom-left quarter in y-down points = top edge at 450.
    assert (box.region.x, box.region.y) == (0, 450)
    assert (box.region.width, box.region.height) == (720, 450)


async def test_recognize_region_is_frame_relative(fake_driver, service):
    fake_driver.set_ocr_boxes(
        [RawTextBox("OK", 0.0, 0.0, 1.0, 1.0, 1.0)])
    (box,) = await service.recognize(region=Region(100, 50, 200, 100))
    assert (box.region.x, box.region.y,
            box.region.width, box.region.height) == (100, 50, 200, 100)


async def test_identical_frame_is_cached(fake_driver, service):
    fake_driver.set_ocr_boxes([RawTextBox("A", 0, 0, 0.1, 0.1, 1.0)])
    await service.recognize()
    await service.recognize()
    assert fake_driver.ocr_calls == 1


async def test_changed_frame_busts_cache(fake_driver, service):
    fake_driver.set_ocr_boxes([RawTextBox("A", 0, 0, 0.1, 0.1, 1.0)])
    await service.recognize()
    fake_driver.draw_rect(Region(0, 0, 400, 400), (200, 10, 10))
    await service.recognize()
    assert fake_driver.ocr_calls == 2
