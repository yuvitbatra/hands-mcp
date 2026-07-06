"""Real-driver desktop-control contract. Gated: HANDS_CONTRACT_MACOS=1.
Requires Screen Recording + Accessibility grants; opens/quits TextEdit."""
import os
import sys

import pytest

from hands.types import ClipboardContent

pytestmark = pytest.mark.skipif(
    os.environ.get("HANDS_CONTRACT_MACOS") != "1"
    or sys.platform != "darwin",
    reason="real macOS driver contract tests are opt-in")


@pytest.fixture
def driver():
    from hands.driver.macos import MacOSDriver
    return MacOSDriver()


def test_permissions_report(driver):
    perms = driver.permissions()
    assert isinstance(perms.screen_recording, bool)
    assert isinstance(perms.accessibility, bool)


def test_clipboard_round_trip(driver):
    saved = driver.clipboard_read()
    try:
        driver.clipboard_write(ClipboardContent("text",
                                                text="hands-m3-test"))
        got = driver.clipboard_read()
        assert got.kind == "text" and got.text == "hands-m3-test"
    finally:
        driver.clipboard_write(saved)


def test_secure_input_flag_is_bool(driver):
    assert isinstance(driver.secure_input_active(), bool)


def test_app_and_window_lifecycle(driver):
    import time
    app = driver.launch_app("com.apple.TextEdit")
    try:
        deadline = time.time() + 15
        wins = []
        while time.time() < deadline:
            wins = [w for w in driver.list_windows(True)
                    if w.pid == app.pid]
            if wins:
                break
            time.sleep(0.3)
        assert wins, "TextEdit opened no window"
        driver.window_perform(wins[0].window_ref, "raise", None)
        tree = driver.ax_tree(app.pid, max_depth=4)
        assert tree.role == "AXApplication"
    finally:
        driver.terminate_app(app.pid, force=False)
