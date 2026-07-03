import hashlib

from hands.config import HandsConfig
from hands.errors import DriverError
from hands.state import ActionRecord, StateManager
from hands.types import Point


def _mgr() -> StateManager:
    cfg = HandsConfig()
    cfg.state.history_len = 3
    return StateManager(cfg)


def test_history_is_bounded_ring_buffer():
    mgr = _mgr()
    for i in range(5):
        mgr.record_action(ActionRecord.ok(f"r{i}", "mouse_move", {}, 0.01))
    hist = mgr.history(10)
    assert len(hist) == 3
    assert hist[-1].request_id == "r4"


def test_typed_text_is_redacted():
    rec = ActionRecord.ok("r1", "keyboard_type", {"text": "hunter2"}, 0.01)
    assert rec.args["text"] == {
        "len": 7,
        "sha256": hashlib.sha256(b"hunter2").hexdigest(),
    }


def test_failed_record_carries_wire_error():
    rec = ActionRecord.failed("r1", "mouse_click", {},
                              DriverError("event tap failed"))
    assert rec.outcome == "DRIVER_ERROR"
    assert rec.error["retryable"] is True


def test_screen_dirty_lifecycle():
    mgr = _mgr()
    assert mgr.screen_dirty is True  # nothing observed yet
    mgr.clear_screen_dirty()
    assert mgr.screen_dirty is False
    mgr.mark_screen_dirty()
    assert mgr.screen_dirty is True


def test_cursor_and_screenshot_meta_roundtrip():
    mgr = _mgr()
    assert mgr.cursor is None
    mgr.cursor = Point(3, 4)
    assert mgr.cursor == Point(3, 4)
    mgr.latest_screenshot_meta = {"screenshot_id": "abc", "ts": 1.0}
    assert mgr.latest_screenshot_meta["screenshot_id"] == "abc"
