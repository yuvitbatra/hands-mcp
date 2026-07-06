"""Full-stack e2e on real macOS (DESIGN §12). Gated: HANDS_E2E_MACOS=1.
Requires Screen Recording + Accessibility; do not run while using the
machine — it moves the mouse.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("HANDS_E2E_MACOS") != "1" or sys.platform != "darwin",
        reason="real-desktop e2e is opt-in (set HANDS_E2E_MACOS=1 on macOS)"),
]

FIXTURE = Path(__file__).parent / "fixture_app.py"


@pytest.fixture
def app_process():
    proc = subprocess.Popen([sys.executable, str(FIXTURE)])
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def container():
    from hands.config import HandsConfig
    from hands.container import Container
    cfg = HandsConfig()
    cfg.driver = "macos"
    cfg.security.profile = "trusted"
    return Container.build(cfg)


async def _call(container, tool, args):
    res = await container.dispatcher.dispatch(tool, args)
    assert res["ok"], f"{tool} failed: {res.get('error')}"
    return res


async def test_click_and_type_flow(app_process, container):
    # 1. Wait for the fixture window to appear
    res = await _call(container, "wait", {
        "condition": {"type": "window_present", "title": "Hands Fixture"},
        "timeout_ms": 15_000})
    assert res["met"], "fixture window never appeared"
    await _call(container, "window_focus", {"title_match": "Hands Fixture"})

    # 2. Find and click INCREMENT (OCR-grounded targeting)
    found = await _call(container, "find_text", {"text": "INCREMENT"})
    assert found["matches"], "OCR could not find the button"
    center = found["matches"][0]["center"]
    await _call(container, "mouse_click",
                {"x": center["x"], "y": center["y"]})

    # 3. Verify the counter advanced
    res = await _call(container, "wait", {
        "condition": {"type": "text_present", "text": "COUNT 1"},
        "timeout_ms": 5_000})
    assert res["met"], "counter did not increment"

    # 4. Type into the entry and verify the echo
    found = await _call(container, "find_text", {"text": "ECHO"})
    echo_center = found["matches"][0]["center"]
    # The entry field is above the echo label; click above it
    await _call(container, "mouse_click",
                {"x": echo_center["x"], "y": echo_center["y"] - 80})
    await _call(container, "keyboard_type", {"text": "abc"})
    res = await _call(container, "wait", {
        "condition": {"type": "text_present", "text": "ECHO abc"},
        "timeout_ms": 5_000})
    assert res["met"], "typed text did not echo"
