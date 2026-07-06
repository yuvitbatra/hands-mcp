"""Layered typed configuration: defaults < HANDS_* env < CLI (DESIGN §4.18)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScreenshotConfig(BaseModel):
    max_dim: int = 1568
    jpeg_quality: int = 80
    cache_ttl_s: float = 2.0


class KeyboardConfig(BaseModel):
    chunk_size: int = 32
    chunk_delay_ms: int = 8


class MouseConfig(BaseModel):
    click_delay_ms: int = 8
    drag_steps: int = 20
    drag_duration_ms: int = 300


class ObserveConfig(BaseModel):
    max_screenshot_age_s: float = 5.0
    require_fresh_default: bool = False


class StateConfig(BaseModel):
    max_screenshots: int = 10
    history_len: int = 200


class OCRConfig(BaseModel):
    languages: list[str] = ["en-US"]
    cache_size: int = 20


class WaiterConfig(BaseModel):
    poll_start_ms: int = 100
    poll_max_ms: int = 500


class VerificationConfig(BaseModel):
    diff_threshold: float = 0.01  # changed_fraction above this = "changed"


class ClipboardConfig(BaseModel):
    restore_delay_ms: int = 500


class SecurityConfig(BaseModel):
    kill_switch_path: Path = Path.home() / ".hands" / "KILL"
    audit_path: Path = Path.home() / ".hands" / "audit.jsonl"
    plugin_allowlist: list[str] | None = None
    profile: Literal["strict", "default", "trusted"] = "default"
    max_actions_per_s: float = 10.0
    deny_apps: list[str] = [
        "com.apple.systempreferences*",
        "com.apple.Passwords*",
        "com.apple.keychainaccess",
        "com.agilebits.onepassword*",
        "com.1password.*",
    ]
    secret_patterns: list[str] = []
    confirmation: Literal["dialog", "deny"] = "dialog"

    def kill_switch_engaged(self) -> bool:
        return self.kill_switch_path.exists()


class AXConfig(BaseModel):
    max_nodes: int = 500


class HandsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HANDS_",
                                      env_nested_delimiter="__")

    driver: Literal["auto", "fake", "macos"] = "auto"
    screenshot: ScreenshotConfig = ScreenshotConfig()
    keyboard: KeyboardConfig = KeyboardConfig()
    mouse: MouseConfig = MouseConfig()
    observe: ObserveConfig = ObserveConfig()
    state: StateConfig = StateConfig()
    security: SecurityConfig = SecurityConfig()
    plugins: dict[str, dict] = {}
    ocr: OCRConfig = OCRConfig()
    waiter: WaiterConfig = WaiterConfig()
    verification: VerificationConfig = VerificationConfig()
    clipboard: ClipboardConfig = ClipboardConfig()
    ax: AXConfig = AXConfig()


def load_config() -> HandsConfig:
    return HandsConfig()
