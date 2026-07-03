from pathlib import Path

from hands.config import HandsConfig, load_config


def test_defaults():
    cfg = HandsConfig()
    assert cfg.driver == "auto"
    assert cfg.screenshot.max_dim == 1568
    assert cfg.keyboard.chunk_size == 32
    assert cfg.mouse.drag_steps == 20
    assert cfg.observe.max_screenshot_age_s == 5.0
    assert cfg.state.history_len == 200


def test_env_overrides_nested(monkeypatch):
    monkeypatch.setenv("HANDS_DRIVER", "fake")
    monkeypatch.setenv("HANDS_SCREENSHOT__MAX_DIM", "800")
    cfg = load_config()
    assert cfg.driver == "fake"
    assert cfg.screenshot.max_dim == 800


def test_kill_switch_reflects_file(tmp_path: Path):
    cfg = HandsConfig()
    cfg.security.kill_switch_path = tmp_path / "KILL"
    assert cfg.security.kill_switch_engaged() is False
    cfg.security.kill_switch_path.touch()
    assert cfg.security.kill_switch_engaged() is True


def test_m2_config_sections():
    cfg = HandsConfig()
    assert cfg.ocr.languages == ["en-US"]
    assert cfg.ocr.cache_size == 20
    assert cfg.waiter.poll_start_ms == 100
    assert cfg.waiter.poll_max_ms == 500
    assert cfg.verification.diff_threshold == 0.01


def test_m3_clipboard_config():
    cfg = HandsConfig()
    assert cfg.clipboard.restore_delay_ms == 500
