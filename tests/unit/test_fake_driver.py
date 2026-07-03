import pytest

from hands.driver.base import MouseEventSpec, RawTextBox
from hands.driver.fake import FakeDriver
from hands.errors import DriverError, TargetNotFoundError
from hands.types import (ClipboardContent, ModifierFlags, MouseButton, Point,
                        Region, WindowInfo)


def test_displays_and_capture_metadata():
    drv = FakeDriver()
    (d,) = drv.displays()
    assert d.is_main and d.scale == 2.0
    assert d.bounds_pt == Region(0, 0, 1440, 900)
    frame = drv.capture(None, None)
    assert frame.bounds_pt == d.bounds_pt
    assert frame.px_per_pt == 2.0
    assert frame.image.size == (2880, 1800)  # physical pixels


def test_region_capture_crops_physical_pixels():
    drv = FakeDriver()
    frame = drv.capture(Region(10, 20, 100, 50), None)
    assert frame.bounds_pt == Region(10, 20, 100, 50)
    assert frame.image.size == (200, 100)


def test_mouse_events_move_cursor_and_record():
    drv = FakeDriver()
    ev = MouseEventSpec(kind="move", at=Point(5, 6), button=MouseButton.LEFT)
    drv.post_mouse(ev)
    assert drv.cursor_position() == Point(5, 6)
    assert drv.pop_events() == [("mouse", ev)]
    assert drv.pop_events() == []  # pop drains


def test_typing_and_keys_record():
    drv = FakeDriver()
    drv.type_unicode("hi")
    drv.post_key(36, True, ModifierFlags.NONE)
    drv.post_key(36, False, ModifierFlags.NONE)
    assert drv.typed_text() == "hi"
    kinds = [e[0] for e in drv.pop_events()]
    assert kinds == ["type", "key", "key"]


def test_fail_next_raises_once_then_recovers():
    drv = FakeDriver()
    drv.fail_next("capture", DriverError("flake"))
    with pytest.raises(DriverError):
        drv.capture(None, None)
    assert drv.capture(None, None).px_per_pt == 2.0


def test_fake_ocr_returns_scripted_boxes():
    drv = FakeDriver()
    boxes = [RawTextBox("Submit", 0.1, 0.2, 0.3, 0.05, 0.99)]
    drv.set_ocr_boxes(boxes)
    frame = drv.capture(None, None)
    assert drv.ocr(frame, ["en-US"]) == boxes
    assert drv.ocr_calls == 1


def test_fake_ocr_fail_injection():
    drv = FakeDriver()
    drv.fail_next("ocr", DriverError("vision unavailable"))
    with pytest.raises(DriverError):
        drv.ocr(drv.capture(None, None), ["en-US"])


def test_draw_rect_changes_captured_pixels():
    drv = FakeDriver()
    before = drv.capture(None, None).image.copy()
    drv.draw_rect(Region(0, 0, 100, 100), (255, 0, 0))
    after = drv.capture(None, None).image
    assert before.getpixel((10, 10)) != after.getpixel((10, 10))


def test_fake_clipboard_round_trip():
    drv = FakeDriver()
    assert drv.clipboard_read().kind == "empty"
    drv.clipboard_write(ClipboardContent("text", text="hello"))
    got = drv.clipboard_read()
    assert got.kind == "text" and got.text == "hello"


def test_fake_secure_input_flag():
    drv = FakeDriver()
    assert drv.secure_input_active() is False
    drv.set_secure_input(True)
    assert drv.secure_input_active() is True


def test_fake_clipboard_fail_injection():
    drv = FakeDriver()
    drv.fail_next("clipboard_read", DriverError("pasteboard busy"))
    with pytest.raises(DriverError):
        drv.clipboard_read()


def _win(drv, title="Doc 1", pid=42, focused=False):
    return drv.add_window("TextEdit", "com.apple.TextEdit", pid, title,
                          Region(10, 10, 800, 600), focused=focused)


def test_add_and_list_windows():
    drv = FakeDriver()
    ref = _win(drv, focused=True)
    (w,) = drv.list_windows(on_screen_only=True)
    assert isinstance(w, WindowInfo)
    assert w.window_ref == ref and w.title == "Doc 1" and w.focused


def test_minimize_hides_from_on_screen_list():
    drv = FakeDriver()
    ref = _win(drv)
    drv.window_perform(ref, "minimize", None)
    assert drv.list_windows(on_screen_only=True) == []
    (w,) = drv.list_windows(on_screen_only=False)
    assert w.minimized
    drv.window_perform(ref, "unminimize", None)
    assert len(drv.list_windows(on_screen_only=True)) == 1


def test_move_resize_raise_close():
    drv = FakeDriver()
    a = _win(drv, "A", focused=True)
    b = _win(drv, "B", pid=43)
    drv.window_perform(b, "move", Region(0, 0, 800, 600))
    drv.window_perform(b, "resize", Region(0, 0, 1024, 768))
    drv.window_perform(b, "raise", None)
    wins = {w.window_ref: w for w in drv.list_windows(False)}
    assert wins[b].bounds == Region(0, 0, 1024, 768)
    assert wins[b].focused and not wins[a].focused
    drv.window_perform(b, "close", None)
    assert [w.window_ref for w in drv.list_windows(False)] == [a]


def test_stale_ref_raises_target_not_found():
    drv = FakeDriver()
    with pytest.raises(TargetNotFoundError):
        drv.window_perform("999:1", "raise", None)
