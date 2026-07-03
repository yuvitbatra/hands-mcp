# Hands Milestone 1 — Core Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable `hands` MCP server over stdio that can screenshot, move/click/drag/scroll the mouse, and type/press keys — fully tested against a fake in-memory driver on any OS, plus a first real macOS driver.

**Architecture:** Layered per `docs/DESIGN.md`: value types → driver protocol (fake + macOS impls) → services (screenshot, mouse, keyboard) → registry + 7-phase dispatcher → thin MCP tools → low-level `mcp.server.Server` over stdio. All OS access goes through the `Driver` protocol so everything except `driver/macos.py` tests on any machine.

**Tech Stack:** Python ≥ 3.12, uv, `mcp` SDK (low-level Server API), Pydantic v2 + pydantic-settings, anyio, Pillow, structlog, pytest + anyio pytest plugin. macOS driver: pyobjc (Quartz) + `/usr/sbin/screencapture` CLI.

## Milestone map (context, not tasks)

- **M1 (this plan):** scaffolding, errors, types, config, retry, fake driver, vision utils, state, coords, screenshot/mouse/keyboard services, registry+dispatcher (allow-all policy stub, minimal audit/metrics), tools, server/CLI, macOS driver v1.
- **M2 (future plan):** OCR (Apple Vision), verification engine, waiter conditions, `find_text`/`verify`/`wait` full tools, ScreenCaptureKit capture.
- **M3 (future plan):** windows/apps/clipboard services + tools, PermissionEngine profiles + confirmation hooks, hash-chained audit, metrics/doctor.
- **M4 (future plan):** plugin system, `execute_sequence`, e2e fixture app, perf/stress suites.

## Global Constraints

- Python `>=3.12`; `src/` layout; package name `hands`; managed with `uv`.
- **No git commits for now (user instruction, 2026-07-03).** Tasks end with a "Verify" step running the full test suite instead of a commit. When the user lifts this, commit once per completed task with `feat:`/`test:` prefixes.
- All coordinates everywhere are **logical points, top-left origin of the main display, y-down** (DESIGN §4.12).
- `stdout` is reserved for the MCP transport. All logging goes to `stderr`. Never `print()` in library code.
- Use `anyio` (not raw `asyncio`); blocking driver calls run via `anyio.to_thread.run_sync`.
- Pydantic argument models use `extra="forbid"`.
- Error codes are the stable wire contract: `INVALID_ARGS`, `PERMISSION_MISSING`, `POLICY_DENIED`, `KILL_SWITCH`, `TIMEOUT`, `TARGET_NOT_FOUND`, `STALE_SCREENSHOT`, `DRIVER_ERROR`, `INTERNAL`.
- Deviation from DESIGN §4.1, agreed here: use the SDK's **low-level `mcp.server.Server`** instead of FastMCP. Registry-driven tool listing and a single dispatch entry point map directly onto `@server.list_tools()` / `@server.call_tool()`; FastMCP's decorator/inference layer would fight the registry. Same SDK, no functional change.
- M1 stubs (upgraded in M3, interfaces already final): `AllowAllPermissions` policy, plain-JSONL `AuditLogger` (no hash chain yet), dict-based `Metrics`.

---

### Task 1: Project scaffolding and packaging

**Files:**
- Modify: `pyproject.toml`
- Delete: `main.py`
- Create: `src/hands/__init__.py`, `tests/conftest.py`, `tests/unit/test_version.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `hands` package with `hands.__version__: str`; pytest runs with the `anyio` plugin (`@pytest.mark.anyio` + `anyio_backend` fixture); `uv run hands` console script entry (wired fully in Task 15).

- [ ] **Step 1: Replace `pyproject.toml`**

```toml
[project]
name = "hands"
version = "0.1.0"
description = "macOS computer-use MCP server for autonomous AI agents"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.2",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "anyio>=4.4",
    "pillow>=10.3",
    "structlog>=24.1",
]

[project.optional-dependencies]
macos = [
    "pyobjc-framework-Quartz>=10.2; sys_platform == 'darwin'",
]

[project.scripts]
hands = "hands.cli:main"

[dependency-groups]
dev = [
    "pytest>=8.2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hands"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Delete the scaffold and create the package**

```bash
rm main.py
mkdir -p src/hands tests/unit
```

`src/hands/__init__.py`:

```python
"""Hands — macOS computer-use MCP server. See docs/DESIGN.md."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `tests/conftest.py`**

```python
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 4: Write the smoke test** — `tests/unit/test_version.py`:

```python
import hands


def test_version_is_exposed():
    assert hands.__version__ == "0.1.0"
```

- [ ] **Step 5: Sync and run**

Run: `uv sync --all-extras && uv run pytest -q`
Expected: `1 passed`

---

### Task 2: Error hierarchy (`errors.py`)

**Files:**
- Create: `src/hands/errors.py`
- Test: `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `HandsError(message, *, details: dict | None = None, remediation: str | None = None)` with class attrs `code: str`, `retryable: bool`, method `to_wire() -> dict`; subclasses `InvalidArgsError`, `PermissionMissingError`, `PolicyDeniedError`, `KillSwitchError`, `TargetNotFoundError` (retryable), `StaleScreenshotError` (retryable), `DriverError` (retryable), `ToolTimeoutError` (retryable, code `TIMEOUT`).

- [ ] **Step 1: Write failing tests** — `tests/unit/test_errors.py`:

```python
import pytest

from hands.errors import (
    DriverError,
    HandsError,
    InvalidArgsError,
    StaleScreenshotError,
    ToolTimeoutError,
)


def test_to_wire_includes_contract_fields():
    err = InvalidArgsError("x out of bounds", details={"x": 99999},
                           remediation="pass clamp=true")
    wire = err.to_wire()
    assert wire == {
        "code": "INVALID_ARGS",
        "message": "x out of bounds",
        "retryable": False,
        "remediation": "pass clamp=true",
        "details": {"x": 99999},
    }


def test_defaults_are_safe():
    err = HandsError("boom")
    assert err.code == "INTERNAL"
    assert err.retryable is False
    assert err.details == {}
    assert err.to_wire()["remediation"] is None


@pytest.mark.parametrize("cls,code,retryable", [
    (DriverError, "DRIVER_ERROR", True),
    (StaleScreenshotError, "STALE_SCREENSHOT", True),
    (ToolTimeoutError, "TIMEOUT", True),
])
def test_retryable_classification(cls, code, retryable):
    err = cls("x")
    assert (err.code, err.retryable) == (code, retryable)


def test_is_an_exception():
    with pytest.raises(HandsError):
        raise DriverError("capture failed")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_errors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.errors'`

- [ ] **Step 3: Implement** — `src/hands/errors.py`:

```python
"""Single exception hierarchy; codes are the wire contract (DESIGN §4.20)."""
from __future__ import annotations

from typing import Any


class HandsError(Exception):
    code: str = "INTERNAL"
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None,
                 remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.remediation = remediation

    def to_wire(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "remediation": self.remediation,
            "details": self.details,
        }


class InvalidArgsError(HandsError):
    code = "INVALID_ARGS"


class PermissionMissingError(HandsError):
    """OS-level TCC grant missing; remediation carries a settings deep link."""
    code = "PERMISSION_MISSING"


class PolicyDeniedError(HandsError):
    code = "POLICY_DENIED"


class KillSwitchError(HandsError):
    code = "KILL_SWITCH"


class TargetNotFoundError(HandsError):
    code = "TARGET_NOT_FOUND"
    retryable = True


class StaleScreenshotError(HandsError):
    code = "STALE_SCREENSHOT"
    retryable = True


class DriverError(HandsError):
    code = "DRIVER_ERROR"
    retryable = True


class ToolTimeoutError(HandsError):
    code = "TIMEOUT"
    retryable = True
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 3: Value types (`types.py`)

**Files:**
- Create: `src/hands/types.py`
- Test: `tests/unit/test_types.py`

**Interfaces:**
- Consumes: `InvalidArgsError` from Task 2.
- Produces (frozen dataclasses unless noted):
  - `Point(x: float, y: float)` with `.offset(dx, dy) -> Point`
  - `Region(x, y, width, height)` with `.center -> Point`, `.contains(p: Point) -> bool`
  - `DisplayInfo(display_id: int, bounds_pt: Region, scale: float, is_main: bool)`
  - `MouseButton` StrEnum: `LEFT/RIGHT/MIDDLE` = `"left"/"right"/"middle"`
  - `ModifierFlags` Flag: `NONE, CMD, SHIFT, ALT, CTRL`
  - `KeyChord(modifiers: ModifierFlags, key: str, keycode: int)` with classmethod `parse(spec: str) -> KeyChord`
  - dicts `KEY_CODES: dict[str, int]`, `MODIFIER_NAMES: dict[str, ModifierFlags]`, `MODIFIER_KEYCODES: dict[ModifierFlags, int]`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_types.py`:

```python
import pytest

from hands.errors import InvalidArgsError
from hands.types import (
    KeyChord,
    ModifierFlags,
    MouseButton,
    Point,
    Region,
)


def test_region_center_and_contains():
    r = Region(10, 20, 100, 50)
    assert r.center == Point(60, 45)
    assert r.contains(Point(10, 20))
    assert r.contains(Point(109.9, 69.9))
    assert not r.contains(Point(110, 70))
    assert not r.contains(Point(9, 20))


def test_point_offset():
    assert Point(1, 2).offset(3, -1) == Point(4, 1)


def test_mouse_button_is_wire_string():
    assert MouseButton("left") is MouseButton.LEFT


def test_keychord_parse_plain_named_key():
    chord = KeyChord.parse("Return")
    assert chord.key == "Return"
    assert chord.keycode == 36
    assert chord.modifiers == ModifierFlags.NONE


def test_keychord_parse_modifiers_and_letter():
    chord = KeyChord.parse("cmd+shift+p")
    assert chord.modifiers == ModifierFlags.CMD | ModifierFlags.SHIFT
    assert chord.key == "p"
    assert chord.keycode == 35


def test_keychord_parse_alias_modifiers():
    assert KeyChord.parse("command+option+s").modifiers == (
        ModifierFlags.CMD | ModifierFlags.ALT
    )


def test_keychord_unknown_key_suggests_near_miss():
    with pytest.raises(InvalidArgsError) as ei:
        KeyChord.parse("cmd+Retrun")
    assert "Return" in str(ei.value.details.get("did_you_mean", []))


def test_keychord_empty_is_invalid():
    with pytest.raises(InvalidArgsError):
        KeyChord.parse("")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_types.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.types'`

- [ ] **Step 3: Implement** — `src/hands/types.py`:

```python
"""Shared value objects. All coordinates are logical points, top-left origin
of the main display, y-down (DESIGN §4.12)."""
from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass

from .errors import InvalidArgsError


class MouseButton(enum.StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ModifierFlags(enum.Flag):
    NONE = 0
    CMD = enum.auto()
    SHIFT = enum.auto()
    ALT = enum.auto()
    CTRL = enum.auto()


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def offset(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True, slots=True)
class Region:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, p: Point) -> bool:
        return (self.x <= p.x < self.x + self.width
                and self.y <= p.y < self.y + self.height)


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    display_id: int
    bounds_pt: Region
    scale: float          # physical px per logical pt (2.0 on Retina)
    is_main: bool


# macOS virtual key codes at ANSI positions. Chords only — text typing uses
# layout-independent unicode injection, never these (DESIGN §4.6).
_LETTER_DIGIT_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

KEY_CODES: dict[str, int] = {
    "Return": 36, "Tab": 48, "Space": 49, "Delete": 51, "Escape": 53,
    "Left": 123, "Right": 124, "Down": 125, "Up": 126,
    "Home": 115, "End": 119, "PageUp": 116, "PageDown": 121,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
    "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
    **_LETTER_DIGIT_CODES,
}

MODIFIER_NAMES: dict[str, ModifierFlags] = {
    "cmd": ModifierFlags.CMD, "command": ModifierFlags.CMD,
    "shift": ModifierFlags.SHIFT,
    "alt": ModifierFlags.ALT, "option": ModifierFlags.ALT,
    "ctrl": ModifierFlags.CTRL, "control": ModifierFlags.CTRL,
}

# Virtual key codes for the modifier keys themselves (left-side variants).
MODIFIER_KEYCODES: dict[ModifierFlags, int] = {
    ModifierFlags.CMD: 55,
    ModifierFlags.SHIFT: 56,
    ModifierFlags.ALT: 58,
    ModifierFlags.CTRL: 59,
}


@dataclass(frozen=True, slots=True)
class KeyChord:
    modifiers: ModifierFlags
    key: str
    keycode: int

    @classmethod
    def parse(cls, spec: str) -> "KeyChord":
        parts = [p for p in spec.split("+") if p]
        if not parts:
            raise InvalidArgsError(f"empty key chord: {spec!r}")
        *mod_parts, key_part = parts
        mods = ModifierFlags.NONE
        for m in mod_parts:
            flag = MODIFIER_NAMES.get(m.lower())
            if flag is None:
                raise InvalidArgsError(
                    f"unknown modifier {m!r} in chord {spec!r}",
                    details={"known": sorted(MODIFIER_NAMES)})
            mods |= flag
        key = key_part if key_part in KEY_CODES else key_part.lower()
        if key not in KEY_CODES:
            close = difflib.get_close_matches(key_part, KEY_CODES, n=3)
            raise InvalidArgsError(
                f"unknown key {key_part!r} in chord {spec!r}",
                details={"did_you_mean": close})
        return cls(modifiers=mods, key=key, keycode=KEY_CODES[key])
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 4: Configuration (`config.py`)

**Files:**
- Create: `src/hands/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `HandsConfig` (pydantic-settings `BaseSettings`, env prefix `HANDS_`, nested delimiter `__`) with fields:
  - `driver: Literal["auto", "fake", "macos"] = "auto"`
  - `screenshot: ScreenshotConfig(max_dim: int = 1568, jpeg_quality: int = 80, cache_ttl_s: float = 2.0)`
  - `keyboard: KeyboardConfig(chunk_size: int = 32, chunk_delay_ms: int = 8)`
  - `mouse: MouseConfig(click_delay_ms: int = 8, drag_steps: int = 20, drag_duration_ms: int = 300)`
  - `observe: ObserveConfig(max_screenshot_age_s: float = 5.0, require_fresh_default: bool = False)`
  - `state: StateConfig(max_screenshots: int = 10, history_len: int = 200)`
  - `security: SecurityConfig(kill_switch_path: Path = ~/.hands/KILL, audit_path: Path = ~/.hands/audit.jsonl)` with method `kill_switch_engaged() -> bool`
  - `load_config() -> HandsConfig`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.config'`

- [ ] **Step 3: Implement** — `src/hands/config.py`:

```python
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


class SecurityConfig(BaseModel):
    kill_switch_path: Path = Path.home() / ".hands" / "KILL"
    audit_path: Path = Path.home() / ".hands" / "audit.jsonl"

    def kill_switch_engaged(self) -> bool:
        return self.kill_switch_path.exists()


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


def load_config() -> HandsConfig:
    return HandsConfig()
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 5: Retry framework (`retry.py`)

**Files:**
- Create: `src/hands/retry.py`
- Test: `tests/unit/test_retry.py`

**Interfaces:**
- Consumes: `HandsError` from Task 2.
- Produces: `RetryPolicy(max_attempts: int = 1, base_delay_s: float = 0.05, max_delay_s: float = 1.0)` with classmethods `read()` (3 attempts), `pre_side_effect()` (3 attempts), `none()` (1 attempt); `async execute_with_retry(fn: Callable[[], Awaitable[dict]], policy: RetryPolicy) -> dict`. **Invariant:** never retries when `err.retryable` is False OR `err.details.get("side_effect")` is truthy (DESIGN §9.8).

- [ ] **Step 1: Write failing tests** — `tests/unit/test_retry.py`:

```python
import pytest

from hands.errors import DriverError, InvalidArgsError
from hands.retry import RetryPolicy, execute_with_retry

pytestmark = pytest.mark.anyio


async def test_succeeds_after_transient_failures():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise DriverError("transient")
        return {"ok": True}

    policy = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)
    assert await execute_with_retry(fn, policy) == {"ok": True}
    assert len(calls) == 3


async def test_non_retryable_error_raises_immediately():
    calls = []

    async def fn():
        calls.append(1)
        raise InvalidArgsError("bad")

    with pytest.raises(InvalidArgsError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 1


async def test_side_effect_flag_blocks_retry_even_if_retryable():
    calls = []

    async def fn():
        calls.append(1)
        raise DriverError("failed mid-click", details={"side_effect": True})

    with pytest.raises(DriverError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 1


async def test_exhausts_attempts_then_raises():
    calls = []

    async def fn():
        calls.append(1)
        raise DriverError("always")

    with pytest.raises(DriverError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 3


def test_policy_presets():
    assert RetryPolicy.read().max_attempts == 3
    assert RetryPolicy.pre_side_effect().max_attempts == 3
    assert RetryPolicy.none().max_attempts == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_retry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.retry'`

- [ ] **Step 3: Implement** — `src/hands/retry.py`:

```python
"""Declarative retries with the left-of-side-effect invariant (DESIGN §4.21)."""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio

from .errors import HandsError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    base_delay_s: float = 0.05
    max_delay_s: float = 1.0

    @classmethod
    def read(cls) -> "RetryPolicy":
        return cls(max_attempts=3)

    @classmethod
    def pre_side_effect(cls) -> "RetryPolicy":
        """Retries only errors raised BEFORE any HID event was posted.
        Services mark ambiguous failures with details['side_effect']=True;
        those are never retried (DESIGN §9.8)."""
        return cls(max_attempts=3)

    @classmethod
    def none(cls) -> "RetryPolicy":
        return cls(max_attempts=1)


async def execute_with_retry(fn: Callable[[], Awaitable[dict]],
                             policy: RetryPolicy) -> dict:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except HandsError as err:
            unsafe = bool(err.details.get("side_effect"))
            if (not err.retryable) or unsafe or attempt >= policy.max_attempts:
                raise
            delay = min(policy.max_delay_s,
                        policy.base_delay_s * 2 ** (attempt - 1))
            await anyio.sleep(random.uniform(0, delay))  # full jitter
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 6: Driver protocol and fake driver (`driver/`)

**Files:**
- Create: `src/hands/driver/__init__.py`, `src/hands/driver/base.py`, `src/hands/driver/fake.py`
- Test: `tests/unit/test_fake_driver.py`
- Modify: `tests/conftest.py` (add `fake_driver` fixture)

**Interfaces:**
- Consumes: types (Task 3), errors (Task 2).
- Produces in `base.py`:
  - `RawFrame(image: PIL.Image.Image, bounds_pt: Region, px_per_pt: float, display_id: int)` (frozen dataclass, `eq=False`)
  - `MouseEventSpec(kind: Literal["move", "down", "up"], at: Point, button: MouseButton, click_count: int = 1, modifiers: ModifierFlags = ModifierFlags.NONE)`
  - `Driver` Protocol (M1 subset): `capture(region: Region | None, display_id: int | None) -> RawFrame`, `displays() -> list[DisplayInfo]`, `cursor_position() -> Point`, `post_mouse(event: MouseEventSpec) -> None`, `post_scroll(at: Point, dx: int, dy: int, pixels: bool) -> None`, `type_unicode(text: str) -> None`, `post_key(keycode: int, down: bool, flags: ModifierFlags) -> None`
- Produces in `fake.py`: `FakeDriver(size_pt=(1440, 900), scale=2.0)` implementing `Driver`, plus test helpers `events: list[tuple]` (entries `("mouse", MouseEventSpec)`, `("scroll", at, dx, dy, pixels)`, `("type", text)`, `("key", keycode, down, flags)`), `pop_events() -> list`, `typed_text() -> str`, `fail_next(op: str, exc: Exception)`.
- Produces in `__init__.py`: re-exports `Driver`, `RawFrame`, `MouseEventSpec`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_fake_driver.py`:

```python
import pytest

from hands.driver.base import MouseEventSpec
from hands.driver.fake import FakeDriver
from hands.errors import DriverError
from hands.types import ModifierFlags, MouseButton, Point, Region


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_fake_driver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.driver'`

- [ ] **Step 3: Implement `src/hands/driver/base.py`**

```python
"""The OS seam. Dumb by design: no policy, retries, or coordinate math
(DESIGN §6.1). M1 exposes the perception + input subset."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from PIL import Image

from ..types import DisplayInfo, ModifierFlags, MouseButton, Point, Region


@dataclass(frozen=True, slots=True, eq=False)
class RawFrame:
    image: Image.Image           # physical pixels
    bounds_pt: Region            # what part of point-space this shows
    px_per_pt: float
    display_id: int


@dataclass(frozen=True, slots=True)
class MouseEventSpec:
    kind: Literal["move", "down", "up"]
    at: Point
    button: MouseButton
    click_count: int = 1
    modifiers: ModifierFlags = ModifierFlags.NONE


@runtime_checkable
class Driver(Protocol):
    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame: ...
    def displays(self) -> list[DisplayInfo]: ...
    def cursor_position(self) -> Point: ...
    def post_mouse(self, event: MouseEventSpec) -> None: ...
    def post_scroll(self, at: Point, dx: int, dy: int,
                    pixels: bool) -> None: ...
    def type_unicode(self, text: str) -> None: ...
    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None: ...
```

- [ ] **Step 4: Implement `src/hands/driver/fake.py`**

```python
"""In-memory virtual desktop for tests (DESIGN §3.1, driver/fake.py)."""
from __future__ import annotations

from PIL import Image

from ..types import DisplayInfo, ModifierFlags, Point, Region
from .base import MouseEventSpec, RawFrame


class FakeDriver:
    def __init__(self, size_pt: tuple[int, int] = (1440, 900),
                 scale: float = 2.0) -> None:
        w, h = size_pt
        self._display = DisplayInfo(display_id=1,
                                    bounds_pt=Region(0, 0, w, h),
                                    scale=scale, is_main=True)
        self._scale = scale
        self._cursor = Point(0, 0)
        self._screen = Image.new("RGB", (int(w * scale), int(h * scale)),
                                 (30, 30, 30))
        self.events: list[tuple] = []
        self._typed: list[str] = []
        self._fail_next: dict[str, Exception] = {}

    # --- test helpers -----------------------------------------------------
    def fail_next(self, op: str, exc: Exception) -> None:
        self._fail_next[op] = exc

    def pop_events(self) -> list[tuple]:
        out, self.events = self.events, []
        return out

    def typed_text(self) -> str:
        return "".join(self._typed)

    def _maybe_fail(self, op: str) -> None:
        exc = self._fail_next.pop(op, None)
        if exc is not None:
            raise exc

    # --- Driver protocol ----------------------------------------------------
    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame:
        self._maybe_fail("capture")
        if region is None:
            return RawFrame(self._screen.copy(), self._display.bounds_pt,
                            self._scale, self._display.display_id)
        s = self._scale
        box = (int(region.x * s), int(region.y * s),
               int((region.x + region.width) * s),
               int((region.y + region.height) * s))
        return RawFrame(self._screen.crop(box), region, s,
                        self._display.display_id)

    def displays(self) -> list[DisplayInfo]:
        return [self._display]

    def cursor_position(self) -> Point:
        return self._cursor

    def post_mouse(self, event: MouseEventSpec) -> None:
        self._maybe_fail("post_mouse")
        self.events.append(("mouse", event))
        self._cursor = event.at

    def post_scroll(self, at: Point, dx: int, dy: int, pixels: bool) -> None:
        self._maybe_fail("post_scroll")
        self.events.append(("scroll", at, dx, dy, pixels))

    def type_unicode(self, text: str) -> None:
        self._maybe_fail("type_unicode")
        self.events.append(("type", text))
        self._typed.append(text)

    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None:
        self._maybe_fail("post_key")
        self.events.append(("key", keycode, down, flags))
```

`src/hands/driver/__init__.py`:

```python
from .base import Driver, MouseEventSpec, RawFrame

__all__ = ["Driver", "MouseEventSpec", "RawFrame"]
```

- [ ] **Step 5: Add the shared fixture** — append to `tests/conftest.py`:

```python
from hands.driver.fake import FakeDriver


@pytest.fixture
def fake_driver():
    return FakeDriver()
```

- [ ] **Step 6: Verify**

Run: `uv run pytest -q`
Expected: all pass (including a protocol conformance you get for free: `FakeDriver` satisfies `Driver` structurally; the contract suite in Task 16 asserts it explicitly).

---

### Task 7: Vision utilities (`services/vision.py`)

**Files:**
- Create: `src/hands/services/__init__.py` (empty), `src/hands/services/vision.py`
- Test: `tests/unit/test_vision.py`

**Interfaces:**
- Consumes: `RawFrame` (Task 6).
- Produces:
  - `downscale(frame: RawFrame, max_dim: int) -> tuple[PIL.Image.Image, float]` — returns (image, resulting `px_per_pt`); no-op if the long edge already fits.
  - `encode(image: PIL.Image.Image, fmt: str, jpeg_quality: int) -> bytes` — `fmt` in `{"png", "jpeg"}`.
  - `perceptual_hash(image: PIL.Image.Image) -> str` — 64-bit average hash as 16 hex chars.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_vision.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_vision.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.services'`

- [ ] **Step 3: Implement** — create empty `src/hands/services/__init__.py`, then `src/hands/services/vision.py`:

```python
"""Pure-Pillow image helpers; no OS dependencies (DESIGN §4.11)."""
from __future__ import annotations

import io

from PIL import Image

from ..driver.base import RawFrame


def downscale(frame: RawFrame, max_dim: int) -> tuple[Image.Image, float]:
    img = frame.image
    long_edge = max(img.size)
    if long_edge <= max_dim:
        return img, frame.px_per_pt
    factor = max_dim / long_edge
    new_size = (round(img.width * factor), round(img.height * factor))
    return img.resize(new_size, Image.LANCZOS), frame.px_per_pt * factor


def encode(image: Image.Image, fmt: str, jpeg_quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpeg":
        image.convert("RGB").save(buf, "JPEG", quality=jpeg_quality)
    else:
        image.save(buf, "PNG")
    return buf.getvalue()


def perceptual_hash(image: Image.Image) -> str:
    """64-bit average hash: grayscale 8x8, threshold at mean."""
    small = image.convert("L").resize((8, 8), Image.LANCZOS)
    pixels = list(small.getdata())
    mean = sum(pixels) / 64
    bits = 0
    for i, p in enumerate(pixels):
        if p >= mean:
            bits |= 1 << i
    return f"{bits:016x}"
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 8: State manager (`state.py`)

**Files:**
- Create: `src/hands/state.py`
- Test: `tests/unit/test_state.py`

**Interfaces:**
- Consumes: `HandsConfig` (Task 4), `HandsError` (Task 2).
- Produces:
  - `ActionRecord(request_id: str, tool: str, args: dict, outcome: str, duration_s: float, error: dict | None)` with classmethods `ok(request_id, tool, args, duration_s)` and `failed(request_id, tool, args, err: HandsError)`; both **redact**: if `args` contains a `text` key, it is replaced by `{"len": int, "sha256": str}`.
  - `StateManager(config: HandsConfig)` with: `record_action(rec: ActionRecord)`, `history(n: int) -> list[ActionRecord]`, `mark_screen_dirty()`, `clear_screen_dirty()`, `screen_dirty: bool` (property, starts True — nothing observed yet), `cursor: Point | None` (get/set), `latest_screenshot_meta: dict | None` (get/set — id, ts, phash; the service owns pixel data).

- [ ] **Step 1: Write failing tests** — `tests/unit/test_state.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.state'`

- [ ] **Step 3: Implement** — `src/hands/state.py`:

```python
"""Session memory: advisory cache, never authority (DESIGN §8)."""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .config import HandsConfig
from .errors import HandsError
from .types import Point


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    text = out.get("text")
    if isinstance(text, str):
        out["text"] = {"len": len(text),
                       "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return out


@dataclass(frozen=True, slots=True)
class ActionRecord:
    request_id: str
    tool: str
    args: dict[str, Any]
    outcome: str
    duration_s: float
    error: dict[str, Any] | None = None
    ts: float = 0.0

    @classmethod
    def ok(cls, request_id: str, tool: str, args: dict[str, Any],
           duration_s: float) -> "ActionRecord":
        return cls(request_id, tool, _redact(args), "ok", duration_s,
                   None, time.monotonic())

    @classmethod
    def failed(cls, request_id: str, tool: str, args: dict[str, Any],
               err: HandsError) -> "ActionRecord":
        return cls(request_id, tool, _redact(args), err.code, 0.0,
                   err.to_wire(), time.monotonic())


class StateManager:
    def __init__(self, config: HandsConfig) -> None:
        self._history: deque[ActionRecord] = deque(
            maxlen=config.state.history_len)
        self._screen_dirty = True   # nothing observed yet
        self.cursor: Point | None = None
        self.latest_screenshot_meta: dict[str, Any] | None = None

    def record_action(self, rec: ActionRecord) -> None:
        self._history.append(rec)

    def history(self, n: int) -> list[ActionRecord]:
        return list(self._history)[-n:]

    @property
    def screen_dirty(self) -> bool:
        return self._screen_dirty

    def mark_screen_dirty(self) -> None:
        self._screen_dirty = True

    def clear_screen_dirty(self) -> None:
        self._screen_dirty = False
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 9: Coordinate mapper (`services/coords.py`)

**Files:**
- Create: `src/hands/services/coords.py`
- Test: `tests/unit/test_coords.py`

**Interfaces:**
- Consumes: types (Task 3), `InvalidArgsError` (Task 2).
- Produces: `CoordinateMapper(displays: list[DisplayInfo])` with:
  - `display_for(p: Point) -> DisplayInfo` (raises `InvalidArgsError` if outside all displays)
  - `clamp(p: Point) -> Point` (clamp into the main display bounds, inclusive-exclusive edge handled by nudging inside by 1 pt)
  - `screenshot_px_to_pt(px: Point, *, bounds_pt: Region, px_per_pt: float) -> Point`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_coords.py`:

```python
import pytest

from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.types import DisplayInfo, Point, Region


@pytest.fixture
def mapper() -> CoordinateMapper:
    return CoordinateMapper(
        [DisplayInfo(1, Region(0, 0, 1440, 900), 2.0, True)])


def test_display_for_inside(mapper):
    assert mapper.display_for(Point(0, 0)).display_id == 1
    assert mapper.display_for(Point(1439.9, 899.9)).display_id == 1


def test_display_for_outside_raises(mapper):
    with pytest.raises(InvalidArgsError):
        mapper.display_for(Point(1440, 900))
    with pytest.raises(InvalidArgsError):
        mapper.display_for(Point(-1, 5))


def test_clamp(mapper):
    assert mapper.clamp(Point(-50, 450)) == Point(0, 450)
    assert mapper.clamp(Point(2000, 2000)) == Point(1439, 899)
    assert mapper.clamp(Point(10, 20)) == Point(10, 20)


def test_screenshot_px_to_pt_full_frame(mapper):
    # 2880x1800 px frame of the whole 1440x900 pt display: px_per_pt = 2
    pt = mapper.screenshot_px_to_pt(Point(2880, 1800),
                                    bounds_pt=Region(0, 0, 1440, 900),
                                    px_per_pt=2.0)
    assert pt == Point(1440, 900)


def test_screenshot_px_to_pt_downscaled_region(mapper):
    # A region starting at (100, 200) pt captured at 0.5 px per pt
    pt = mapper.screenshot_px_to_pt(Point(50, 10),
                                    bounds_pt=Region(100, 200, 400, 300),
                                    px_per_pt=0.5)
    assert pt == Point(200, 220)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_coords.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.services.coords'`

- [ ] **Step 3: Implement** — `src/hands/services/coords.py`:

```python
"""All coordinate conversions live here and nowhere else (DESIGN §4.12)."""
from __future__ import annotations

from ..errors import InvalidArgsError
from ..types import DisplayInfo, Point, Region


class CoordinateMapper:
    def __init__(self, displays: list[DisplayInfo]) -> None:
        if not displays:
            raise InvalidArgsError("no displays reported by driver")
        self._displays = displays
        self._main = next(d for d in displays if d.is_main)

    def display_for(self, p: Point) -> DisplayInfo:
        for d in self._displays:
            if d.bounds_pt.contains(p):
                return d
        raise InvalidArgsError(
            f"point ({p.x}, {p.y}) is outside all displays",
            details={"main_bounds": vars(self._main.bounds_pt)},
            remediation="take a screenshot and recompute, or pass clamp=true")

    def clamp(self, p: Point) -> Point:
        b = self._main.bounds_pt
        x = min(max(p.x, b.x), b.x + b.width - 1)
        y = min(max(p.y, b.y), b.y + b.height - 1)
        return Point(x, y)

    def screenshot_px_to_pt(self, px: Point, *, bounds_pt: Region,
                            px_per_pt: float) -> Point:
        return Point(bounds_pt.x + px.x / px_per_pt,
                     bounds_pt.y + px.y / px_per_pt)
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 10: Screenshot service (`services/screenshot.py`)

**Files:**
- Create: `src/hands/services/screenshot.py`
- Test: `tests/unit/test_screenshot_service.py`

**Interfaces:**
- Consumes: `Driver`, `RawFrame` (Task 6), `StateManager` (Task 8), vision utils (Task 7), `HandsConfig` (Task 4), `TargetNotFoundError` (Task 2).
- Produces:
  - `Screenshot(screenshot_id: str, data: bytes, fmt: str, ts: float, bounds_pt: Region, px_per_pt: float, display_id: int, phash: str, cached: bool = False)` (frozen dataclass) with `.meta() -> dict` (everything except `data`, with `bounds_pt` as a dict).
  - `ScreenshotService(driver, state, config)` with `async capture(region=None, display_id=None, fmt="png", max_dim=None, fresh=False) -> Screenshot` and `get(screenshot_id: str) -> Screenshot`.
  - Cache rule: a full-screen (region=None) capture is served from cache iff not `fresh`, screen not dirty, previous capture was full-screen, and age < `cache_ttl_s`. A successful capture calls `state.clear_screen_dirty()` and sets `state.latest_screenshot_meta`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_screenshot_service.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.errors import TargetNotFoundError
from hands.services.screenshot import ScreenshotService
from hands.state import StateManager
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    return ScreenshotService(fake_driver, StateManager(cfg), cfg), fake_driver


async def test_capture_returns_metadata_and_png(svc):
    service, _ = svc
    shot = await service.capture()
    assert shot.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot.px_per_pt <= 2.0            # downscaled to max_dim
    assert shot.bounds_pt == Region(0, 0, 1440, 900)
    assert shot.cached is False
    assert len(shot.phash) == 16


async def test_capture_downscales_to_max_dim(svc):
    service, _ = svc
    shot = await service.capture(max_dim=1440)
    # 2880 px long edge -> 1440: px_per_pt drops from 2.0 to 1.0
    assert shot.px_per_pt == 1.0


async def test_second_capture_hits_cache(svc):
    service, driver = svc
    first = await service.capture()
    second = await service.capture()
    assert second.cached is True
    assert second.screenshot_id == first.screenshot_id
    # only one real driver capture happened
    assert len([e for e in driver.pop_events()]) == 0  # capture isn't an event
    assert first.data == second.data


async def test_dirty_screen_busts_cache(svc):
    service, _ = svc
    first = await service.capture()
    service._state.mark_screen_dirty()
    second = await service.capture()
    assert second.cached is False
    assert second.screenshot_id != first.screenshot_id


async def test_fresh_flag_busts_cache(svc):
    service, _ = svc
    first = await service.capture()
    second = await service.capture(fresh=True)
    assert second.cached is False


async def test_capture_clears_dirty_and_records_meta(svc):
    service, _ = svc
    state = service._state
    assert state.screen_dirty is True
    shot = await service.capture()
    assert state.screen_dirty is False
    assert state.latest_screenshot_meta["screenshot_id"] == shot.screenshot_id


async def test_get_unknown_id_raises(svc):
    service, _ = svc
    with pytest.raises(TargetNotFoundError):
        service.get("nope")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_screenshot_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.services.screenshot'`

- [ ] **Step 3: Implement** — `src/hands/services/screenshot.py`:

```python
"""Capture, scale, encode, cache (DESIGN §4.4)."""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import TargetNotFoundError
from ..state import StateManager
from ..types import Region
from .vision import downscale, encode, perceptual_hash


@dataclass(frozen=True, slots=True)
class Screenshot:
    screenshot_id: str
    data: bytes
    fmt: str
    ts: float
    bounds_pt: Region
    px_per_pt: float
    display_id: int
    phash: str
    cached: bool = False

    def meta(self) -> dict[str, Any]:
        return {
            "screenshot_id": self.screenshot_id,
            "fmt": self.fmt,
            "ts": self.ts,
            "bounds_pt": {"x": self.bounds_pt.x, "y": self.bounds_pt.y,
                          "width": self.bounds_pt.width,
                          "height": self.bounds_pt.height},
            "px_per_pt": self.px_per_pt,
            "display_id": self.display_id,
            "phash": self.phash,
            "cached": self.cached,
        }


class ScreenshotService:
    def __init__(self, driver: Driver, state: StateManager,
                 config: HandsConfig) -> None:
        self._driver = driver
        self._state = state
        self._cfg = config.screenshot
        self._store: OrderedDict[str, Screenshot] = OrderedDict()
        self._max_store = config.state.max_screenshots
        self._last_full: Screenshot | None = None

    async def capture(self, region: Region | None = None,
                      display_id: int | None = None, fmt: str = "png",
                      max_dim: int | None = None,
                      fresh: bool = False) -> Screenshot:
        if region is None and not fresh and self._cache_valid():
            return replace(self._last_full, cached=True)

        raw = await anyio.to_thread.run_sync(
            self._driver.capture, region, display_id)
        img, px_per_pt = downscale(raw, max_dim or self._cfg.max_dim)
        data = encode(img, fmt, self._cfg.jpeg_quality)
        shot = Screenshot(
            screenshot_id=uuid.uuid4().hex[:12], data=data, fmt=fmt,
            ts=time.monotonic(), bounds_pt=raw.bounds_pt,
            px_per_pt=px_per_pt, display_id=raw.display_id,
            phash=perceptual_hash(img))
        self._remember(shot, full=region is None)
        return shot

    def get(self, screenshot_id: str) -> Screenshot:
        try:
            return self._store[screenshot_id]
        except KeyError:
            raise TargetNotFoundError(
                f"screenshot {screenshot_id!r} not found (evicted?)",
                remediation="take a new screenshot") from None

    def _cache_valid(self) -> bool:
        last = self._last_full
        return (last is not None
                and not self._state.screen_dirty
                and time.monotonic() - last.ts < self._cfg.cache_ttl_s)

    def _remember(self, shot: Screenshot, *, full: bool) -> None:
        self._store[shot.screenshot_id] = shot
        while len(self._store) > self._max_store:
            self._store.popitem(last=False)
        if full:
            self._last_full = shot
        self._state.clear_screen_dirty()
        self._state.latest_screenshot_meta = shot.meta()
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 11: Mouse service (`services/mouse.py`)

**Files:**
- Create: `src/hands/services/mouse.py`
- Test: `tests/unit/test_mouse_service.py`

**Interfaces:**
- Consumes: `Driver`, `MouseEventSpec` (Task 6), `CoordinateMapper` (Task 9), `StateManager` (Task 8), `HandsConfig` (Task 4), errors (Task 2).
- Produces:
  - `ClickResult(cursor: Point)` frozen dataclass.
  - `MouseService(driver, coords, state, config)` with:
    - `async move(to: Point, duration_ms: int = 0, clamp: bool = False) -> Point`
    - `async click(at: Point | None, button: MouseButton = LEFT, count: int = 1, modifiers: ModifierFlags = NONE, clamp: bool = False) -> ClickResult`
    - `async drag(path: list[Point], duration_ms: int | None = None, button: MouseButton = LEFT) -> None` (≥ `config.mouse.drag_steps` interpolated moves; **button-up always posted**, even on mid-drag failure)
    - `async scroll(at: Point | None, dx: int, dy: int, pixels: bool = False) -> None`
  - Failure contract: errors raised after the first posted event carry `details["side_effect"] = True`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_mouse_service.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.errors import DriverError, InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.mouse import MouseService
from hands.state import StateManager
from hands.types import MouseButton, Point

pytestmark = pytest.mark.anyio


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    cfg.mouse.click_delay_ms = 0  # keep tests fast
    state = StateManager(cfg)
    mapper = CoordinateMapper(fake_driver.displays())
    return MouseService(fake_driver, mapper, state, cfg), fake_driver, state


async def test_move_posts_event_and_updates_state(svc):
    service, driver, state = svc
    got = await service.move(Point(100, 200))
    assert got == Point(100, 200)
    (kind, ev), = driver.pop_events()
    assert (kind, ev.kind, ev.at) == ("mouse", "move", Point(100, 200))
    assert state.cursor == Point(100, 200)


async def test_move_out_of_bounds_rejected_without_side_effect(svc):
    service, driver, _ = svc
    with pytest.raises(InvalidArgsError) as ei:
        await service.move(Point(99999, 5))
    assert not ei.value.details.get("side_effect")
    assert driver.pop_events() == []


async def test_move_clamp(svc):
    service, _, _ = svc
    assert await service.move(Point(99999, 5), clamp=True) == Point(1439, 5)


async def test_click_sequence_is_move_down_up(svc):
    service, driver, _ = svc
    result = await service.click(Point(10, 10))
    assert result.cursor == Point(10, 10)
    kinds = [ev.kind for _, ev in driver.pop_events()]
    assert kinds == ["move", "down", "up"]


async def test_double_click_sets_click_count(svc):
    service, driver, _ = svc
    await service.click(Point(10, 10), count=2)
    events = [ev for _, ev in driver.pop_events()]
    downs = [e for e in events if e.kind == "down"]
    assert [d.click_count for d in downs] == [1, 2]


async def test_right_click_button(svc):
    service, driver, _ = svc
    await service.click(Point(10, 10), button=MouseButton.RIGHT)
    events = [ev for _, ev in driver.pop_events()]
    assert all(e.button is MouseButton.RIGHT for e in events)


async def test_drag_interpolates_and_ends_with_up(svc):
    service, driver, _ = svc
    await service.drag([Point(0, 0), Point(100, 100)], duration_ms=0)
    events = [ev for _, ev in driver.pop_events()]
    assert events[0].kind == "move"          # position at start
    assert events[1].kind == "down"
    assert events[-1].kind == "up"
    moves_during = [e for e in events[2:-1] if e.kind == "move"]
    assert len(moves_during) >= 20           # config.mouse.drag_steps
    assert events[-1].at == Point(100, 100)


async def test_drag_failure_still_releases_button(svc):
    service, driver, _ = svc

    async def run():
        await service.drag([Point(0, 0), Point(100, 100)], duration_ms=0)

    # Fail one of the interpolated moves mid-drag.
    driver.post_mouse_call_count = 0
    original = driver.post_mouse

    def flaky(event):
        driver.post_mouse_call_count += 1
        if driver.post_mouse_call_count == 5:
            raise DriverError("flake")
        original(event)

    driver.post_mouse = flaky
    with pytest.raises(DriverError) as ei:
        await run()
    assert ei.value.details["side_effect"] is True
    events = [ev for _, ev in driver.pop_events()]
    assert events[-1].kind == "up"           # phantom drag prevented


async def test_scroll_positions_then_scrolls(svc):
    service, driver, _ = svc
    await service.scroll(Point(50, 50), dx=0, dy=-3)
    events = driver.pop_events()
    assert events[0][1].kind == "move"
    assert events[1] == ("scroll", Point(50, 50), 0, -3, False)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_mouse_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.services.mouse'`

- [ ] **Step 3: Implement** — `src/hands/services/mouse.py`:

```python
"""Mouse primitives: move/click/drag/scroll (DESIGN §4.5)."""
from __future__ import annotations

from dataclasses import dataclass

import anyio

from ..config import HandsConfig
from ..driver.base import Driver, MouseEventSpec
from ..errors import HandsError
from ..state import StateManager
from ..types import ModifierFlags, MouseButton, Point
from .coords import CoordinateMapper


@dataclass(frozen=True, slots=True)
class ClickResult:
    cursor: Point


class MouseService:
    def __init__(self, driver: Driver, coords: CoordinateMapper,
                 state: StateManager, config: HandsConfig) -> None:
        self._driver = driver
        self._coords = coords
        self._state = state
        self._cfg = config.mouse

    async def move(self, to: Point, duration_ms: int = 0,
                   clamp: bool = False) -> Point:
        to = self._resolve(to, clamp)
        steps = max(1, duration_ms // 16)  # ~60 Hz interpolation
        start = self._state.cursor or self._driver.cursor_position()
        posted = 0
        try:
            for i in range(1, steps + 1):
                t = i / steps
                p = Point(start.x + (to.x - start.x) * t,
                          start.y + (to.y - start.y) * t)
                await self._post(MouseEventSpec("move", p, MouseButton.LEFT))
                posted += 1
                if steps > 1:
                    await anyio.sleep(duration_ms / steps / 1000)
        except HandsError as err:
            if posted:
                err.details["side_effect"] = True
            raise
        self._state.cursor = to
        return to

    async def click(self, at: Point | None,
                    button: MouseButton = MouseButton.LEFT, count: int = 1,
                    modifiers: ModifierFlags = ModifierFlags.NONE,
                    clamp: bool = False) -> ClickResult:
        pos = (await self.move(at, clamp=clamp) if at is not None
               else self._driver.cursor_position())
        delay = self._cfg.click_delay_ms / 1000
        posted = 0
        try:
            for n in range(1, count + 1):
                await self._post(MouseEventSpec("down", pos, button,
                                                click_count=n,
                                                modifiers=modifiers))
                posted += 1
                await anyio.sleep(delay)
                await self._post(MouseEventSpec("up", pos, button,
                                                click_count=n,
                                                modifiers=modifiers))
                posted += 1
                await anyio.sleep(delay)
        except HandsError as err:
            if posted:
                err.details["side_effect"] = True
            raise
        self._state.cursor = pos
        return ClickResult(cursor=pos)

    async def drag(self, path: list[Point], duration_ms: int | None = None,
                   button: MouseButton = MouseButton.LEFT) -> None:
        if len(path) < 2:
            from ..errors import InvalidArgsError
            raise InvalidArgsError("drag path needs at least 2 points")
        pts = [self._resolve(p, clamp=False) for p in path]
        duration = (self._cfg.drag_duration_ms if duration_ms is None
                    else duration_ms)
        await self.move(pts[0])
        await self._post(MouseEventSpec("down", pts[0], button))
        end = pts[0]
        try:
            steps = max(self._cfg.drag_steps, len(pts) - 1)
            waypoints = _interpolate(pts, steps)
            for p in waypoints:
                await self._post(MouseEventSpec("move", p, button))
                end = p
                if duration:
                    await anyio.sleep(duration / steps / 1000)
        except HandsError as err:
            err.details["side_effect"] = True
            err.details["released_at"] = {"x": end.x, "y": end.y}
            raise
        finally:
            # Never leave a phantom drag (DESIGN §5.4).
            self._driver.post_mouse(MouseEventSpec("up", end, button))
            self._state.cursor = end

    async def scroll(self, at: Point | None, dx: int, dy: int,
                     pixels: bool = False) -> None:
        if at is not None:
            await self.move(at)
        pos = at or self._driver.cursor_position()
        try:
            await anyio.to_thread.run_sync(
                self._driver.post_scroll, pos, dx, dy, pixels)
        except HandsError as err:
            if at is not None:
                err.details["side_effect"] = True  # we already moved
            raise

    def _resolve(self, p: Point, clamp: bool) -> Point:
        if clamp:
            return self._coords.clamp(p)
        self._coords.display_for(p)   # raises InvalidArgsError if outside
        return p

    async def _post(self, ev: MouseEventSpec) -> None:
        await anyio.to_thread.run_sync(self._driver.post_mouse, ev)


def _interpolate(pts: list[Point], steps: int) -> list[Point]:
    """Evenly interpolate `steps` waypoints along the polyline `pts`."""
    out: list[Point] = []
    segs = len(pts) - 1
    per_seg = max(1, steps // segs)
    for i in range(segs):
        a, b = pts[i], pts[i + 1]
        for j in range(1, per_seg + 1):
            t = j / per_seg
            out.append(Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))
    if out and out[-1] != pts[-1]:
        out.append(pts[-1])
    return out
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass. Note the drag-failure test monkeypatches `post_mouse` directly — `fail_next` fires on the *next* call, which would hit the initial positioning move instead of a mid-drag one.

---

### Task 12: Keyboard service (`services/keyboard.py`)

**Files:**
- Create: `src/hands/services/keyboard.py`
- Test: `tests/unit/test_keyboard_service.py`

**Interfaces:**
- Consumes: `Driver` (Task 6), types (Task 3: `KeyChord`, `ModifierFlags`, `MODIFIER_KEYCODES`), `HandsConfig` (Task 4), errors (Task 2).
- Produces: `KeyboardService(driver, config)` with:
  - `async type_text(text: str, chunk_delay_ms: int | None = None) -> int` (returns chars typed; mid-stream failure carries `details.chars_typed` and `details.side_effect=True`)
  - `async press(chord: KeyChord, repeat: int = 1) -> None` (holds modifiers, taps key, **always** releases — even on failure)
  - `release_all() -> None` (synchronous; posts key-up for every held modifier)

- [ ] **Step 1: Write failing tests** — `tests/unit/test_keyboard_service.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.errors import DriverError
from hands.services.keyboard import KeyboardService
from hands.types import KeyChord, ModifierFlags

pytestmark = pytest.mark.anyio

CMD_KEYCODE = 55
SHIFT_KEYCODE = 56


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    cfg.keyboard.chunk_delay_ms = 0
    cfg.keyboard.chunk_size = 4
    return KeyboardService(fake_driver, cfg), fake_driver


async def test_type_text_chunks_and_counts(svc):
    service, driver = svc
    n = await service.type_text("hello world")
    assert n == 11
    assert driver.typed_text() == "hello world"
    chunks = [e[1] for e in driver.pop_events() if e[0] == "type"]
    assert chunks == ["hell", "o wo", "rld"]


async def test_type_text_midstream_failure_reports_progress(svc):
    service, driver = svc
    calls = {"n": 0}
    original = driver.type_unicode

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise DriverError("dropped")
        original(text)

    driver.type_unicode = flaky
    with pytest.raises(DriverError) as ei:
        await service.type_text("hello world")
    assert ei.value.details["chars_typed"] == 4
    assert ei.value.details["side_effect"] is True


async def test_press_holds_then_releases_modifiers(svc):
    service, driver = svc
    await service.press(KeyChord.parse("cmd+shift+s"))
    keys = [e for e in driver.pop_events() if e[0] == "key"]
    downs = [(k, d) for _, k, d, _ in keys]
    # modifiers down, key down+up, modifiers up (order within mods stable)
    assert (CMD_KEYCODE, True) in downs and (SHIFT_KEYCODE, True) in downs
    assert (CMD_KEYCODE, False) in downs and (SHIFT_KEYCODE, False) in downs
    assert downs.index((CMD_KEYCODE, False)) > downs.index((1, True))  # 's'=1


async def test_modifiers_released_even_when_key_post_fails(svc):
    service, driver = svc
    calls = {"n": 0}
    original = driver.post_key

    def flaky(keycode, down, flags):
        calls["n"] += 1
        if keycode not in (CMD_KEYCODE, SHIFT_KEYCODE) and down:
            raise DriverError("tap failed")
        original(keycode, down, flags)

    driver.post_key = flaky
    with pytest.raises(DriverError):
        await service.press(KeyChord.parse("cmd+s"))
    keys = [(k, d) for e, k, d, _ in driver.pop_events() if e == "key"]
    assert (CMD_KEYCODE, False) in keys      # released despite failure


async def test_repeat(svc):
    service, driver = svc
    await service.press(KeyChord.parse("Down"), repeat=3)
    keys = [e for e in driver.pop_events() if e[0] == "key"]
    assert len([k for k in keys if k[2] is True]) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_keyboard_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.services.keyboard'`

- [ ] **Step 3: Implement** — `src/hands/services/keyboard.py`:

```python
"""Keyboard: layout-safe unicode typing + real-keycode chords (DESIGN §4.6)."""
from __future__ import annotations

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import HandsError
from ..types import KeyChord, MODIFIER_KEYCODES, ModifierFlags


class KeyboardService:
    def __init__(self, driver: Driver, config: HandsConfig) -> None:
        self._driver = driver
        self._cfg = config.keyboard
        self._held: list[ModifierFlags] = []   # invariant: mirrors reality

    async def type_text(self, text: str,
                        chunk_delay_ms: int | None = None) -> int:
        delay = (self._cfg.chunk_delay_ms if chunk_delay_ms is None
                 else chunk_delay_ms) / 1000
        typed = 0
        for chunk in _chunks(text, self._cfg.chunk_size):
            try:
                await anyio.to_thread.run_sync(
                    self._driver.type_unicode, chunk)
            except HandsError as err:
                err.details["chars_typed"] = typed
                err.details["side_effect"] = typed > 0
                raise
            typed += len(chunk)
            if delay:
                await anyio.sleep(delay)
        return typed

    async def press(self, chord: KeyChord, repeat: int = 1) -> None:
        try:
            self._hold(chord.modifiers)
            for _ in range(repeat):
                await anyio.to_thread.run_sync(
                    self._driver.post_key, chord.keycode, True,
                    chord.modifiers)
                await anyio.to_thread.run_sync(
                    self._driver.post_key, chord.keycode, False,
                    chord.modifiers)
        finally:
            self.release_all()   # never leave a modifier held (DESIGN §4.6)

    def release_all(self) -> None:
        """Synchronous so shutdown paths can call it (DESIGN §2.6)."""
        while self._held:
            flag = self._held.pop()
            try:
                self._driver.post_key(MODIFIER_KEYCODES[flag], False, flag)
            except Exception:  # noqa: BLE001 — best-effort during teardown
                pass

    def _hold(self, mods: ModifierFlags) -> None:
        for flag in (ModifierFlags.CMD, ModifierFlags.SHIFT,
                     ModifierFlags.ALT, ModifierFlags.CTRL):
            if flag in mods:
                self._driver.post_key(MODIFIER_KEYCODES[flag], True, flag)
                self._held.append(flag)


def _chunks(s: str, n: int):
    for i in range(0, len(s), n):
        yield s[i:i + n]
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 13: Registry, policy stub, audit, metrics, and the dispatcher

**Files:**
- Create: `src/hands/registry.py`, `src/hands/permissions.py`, `src/hands/audit.py`, `src/hands/metrics.py`, `src/hands/dispatcher.py`
- Test: `tests/unit/test_registry.py`, `tests/integration/test_dispatcher.py` (create `tests/integration/` dir)

**Interfaces:**
- Consumes: errors (2), config (4), retry (5), state (8).
- Produces:
  - `ToolSpec(name, description, args_model: type[BaseModel], handler: Callable[[BaseModel, Any], Awaitable[dict]], policy_class: Literal["read","act","sensitive"] = "act", retry: RetryPolicy = RetryPolicy.pre_side_effect(), idempotent: bool = False)`; `ToolRegistry` with `register/get/list_specs/to_mcp_tools`.
  - `ActionDescriptor(tool: str, policy_class: str)`; decisions `Allowed()`, `Denied(reason)` each with `raise_if_denied()`; `AllowAllPermissions` (M1 stub; M3 replaces with rule-based `PermissionEngine`, same `authorize(action)` signature).
  - `AuditLogger(config)` with `record(event: dict)` (JSONL append) and `flush()`.
  - `Metrics()` with `inc(name, **labels)` and `snapshot() -> dict`.
  - `Dispatcher(registry, permissions, state, audit, metrics, config)` with `async dispatch(tool_name: str, raw_args: dict, ctx: Any = None) -> dict` — always returns an envelope, never raises.

- [ ] **Step 1: Write failing registry tests** — `tests/unit/test_registry.py`:

```python
import pytest
from pydantic import BaseModel

from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy


class NoArgs(BaseModel, extra="forbid"):
    pass


async def _noop(args, ctx):
    return {}


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="d", args_model=NoArgs,
                    handler=_noop, policy_class="read",
                    retry=RetryPolicy.none(), idempotent=True)


def test_register_get_and_list():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    assert reg.get("a").name == "a"
    assert [s.name for s in reg.list_specs()] == ["a"]


def test_duplicate_name_rejected():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    with pytest.raises(ValueError):
        reg.register(_spec("a"))


def test_unknown_tool_is_invalid_args():
    from hands.errors import InvalidArgsError
    with pytest.raises(InvalidArgsError):
        ToolRegistry().get("nope")


def test_to_mcp_tools_serializes_schema():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    (tool,) = reg.to_mcp_tools()
    assert tool["name"] == "a"
    assert tool["inputSchema"]["additionalProperties"] is False
```

- [ ] **Step 2: Write failing dispatcher tests** — `tests/integration/test_dispatcher.py`:

```python
import anyio
import pytest
from pydantic import BaseModel

from hands.audit import AuditLogger
from hands.config import HandsConfig
from hands.dispatcher import Dispatcher
from hands.errors import DriverError
from hands.metrics import Metrics
from hands.permissions import AllowAllPermissions, Denied
from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy
from hands.state import StateManager

pytestmark = pytest.mark.anyio


class NoArgs(BaseModel, extra="forbid"):
    pass


class FreshArgs(BaseModel, extra="forbid"):
    require_fresh_screenshot: bool | None = None


class DenyAll:
    def authorize(self, action):
        return Denied("policy says no")


def make(tmp_path, specs, permissions=None):
    cfg = HandsConfig()
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    reg = ToolRegistry()
    for s in specs:
        reg.register(s)
    state = StateManager(cfg)
    disp = Dispatcher(reg, permissions or AllowAllPermissions(), state,
                      AuditLogger(cfg), Metrics(), cfg)
    return disp, state, cfg


def spec(name, handler, *, policy="act", retry=None, model=NoArgs):
    return ToolSpec(name=name, description="d", args_model=model,
                    handler=handler, policy_class=policy,
                    retry=retry or RetryPolicy.none())


async def ok_handler(args, ctx):
    return {"answer": 42}


async def test_happy_path_envelope(tmp_path):
    disp, state, _ = make(tmp_path, [spec("t", ok_handler)])
    res = await disp.dispatch("t", {})
    assert res["ok"] is True and res["answer"] == 42
    assert "request_id" in res
    assert state.history(1)[0].outcome == "ok"


async def test_unknown_tool(tmp_path):
    disp, _, _ = make(tmp_path, [])
    res = await disp.dispatch("nope", {})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_ARGS"


async def test_extra_field_rejected(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler)])
    res = await disp.dispatch("t", {"surprise": 1})
    assert res["error"]["code"] == "INVALID_ARGS"
    assert res["error"]["retryable"] is False


async def test_kill_switch_blocks(tmp_path):
    disp, _, cfg = make(tmp_path, [spec("t", ok_handler)])
    cfg.security.kill_switch_path.touch()
    res = await disp.dispatch("t", {})
    assert res["error"]["code"] == "KILL_SWITCH"


async def test_policy_denial(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler)],
                      permissions=DenyAll())
    res = await disp.dispatch("t", {})
    assert res["error"]["code"] == "POLICY_DENIED"


async def test_transient_failure_retried_for_read_tools(tmp_path):
    calls = []

    async def flaky(args, ctx):
        calls.append(1)
        if len(calls) < 2:
            raise DriverError("flake")
        return {}

    disp, _, _ = make(tmp_path, [spec("t", flaky, policy="read",
                                      retry=RetryPolicy.read())])
    res = await disp.dispatch("t", {})
    assert res["ok"] is True and len(calls) == 2


async def test_act_marks_screen_dirty_read_does_not(tmp_path):
    disp, state, _ = make(tmp_path, [spec("a", ok_handler, policy="act"),
                                     spec("r", ok_handler, policy="read")])
    state.clear_screen_dirty()
    await disp.dispatch("r", {})
    assert state.screen_dirty is False
    await disp.dispatch("a", {})
    assert state.screen_dirty is True


async def test_act_tools_serialize_on_the_action_lock(tmp_path):
    log = []

    async def slow(args, ctx):
        log.append("enter")
        await anyio.sleep(0.02)
        log.append("exit")
        return {}

    disp, _, _ = make(tmp_path, [spec("slow", slow)])
    async with anyio.create_task_group() as tg:
        tg.start_soon(disp.dispatch, "slow", {})
        tg.start_soon(disp.dispatch, "slow", {})
    assert log == ["enter", "exit", "enter", "exit"]


async def test_staleness_gate(tmp_path):
    disp, _, _ = make(tmp_path, [spec("t", ok_handler, model=FreshArgs)])
    res = await disp.dispatch("t", {"require_fresh_screenshot": True})
    assert res["error"]["code"] == "STALE_SCREENSHOT"
    assert res["error"]["retryable"] is True


async def test_audit_line_written(tmp_path):
    disp, _, cfg = make(tmp_path, [spec("t", ok_handler)])
    await disp.dispatch("t", {})
    lines = cfg.security.audit_path.read_text().strip().splitlines()
    assert len(lines) == 1 and '"t"' in lines[0]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_registry.py tests/integration -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.registry'`

- [ ] **Step 4: Implement `src/hands/registry.py`**

```python
"""ToolSpecs are data, not code (DESIGN §4.2)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .errors import InvalidArgsError
from .retry import RetryPolicy

Handler = Callable[[BaseModel, Any], Awaitable[dict[str, Any]]]
PolicyClass = Literal["read", "act", "sensitive"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str          # written for LLM consumption
    args_model: type[BaseModel]
    handler: Handler
    policy_class: PolicyClass = "act"
    retry: RetryPolicy = field(default_factory=RetryPolicy.pre_side_effect)
    idempotent: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise InvalidArgsError(
                f"unknown tool: {name}",
                details={"known": sorted(self._specs)}) from None

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def to_mcp_tools(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "description": s.description,
                 "inputSchema": s.args_model.model_json_schema()}
                for s in self._specs.values()]
```

- [ ] **Step 5: Implement `src/hands/permissions.py`** (M1 stub, M3-final interface)

```python
"""Policy decisions. M1 ships AllowAllPermissions; the rule-based engine
(DESIGN §13.3) lands in M3 behind the same authorize() signature."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import PolicyDeniedError


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    tool: str
    policy_class: str


@dataclass(frozen=True, slots=True)
class Allowed:
    def raise_if_denied(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Denied:
    reason: str

    def raise_if_denied(self) -> None:
        raise PolicyDeniedError(self.reason)


class AllowAllPermissions:
    def authorize(self, action: ActionDescriptor) -> Allowed | Denied:
        return Allowed()
```

- [ ] **Step 6: Implement `src/hands/audit.py` and `src/hands/metrics.py`**

`src/hands/audit.py` (plain JSONL now; hash chain in M3):

```python
"""Append-only JSONL audit log (DESIGN §13.6; hash chaining lands in M3)."""
from __future__ import annotations

import json
import time
from typing import Any

from .config import HandsConfig


class AuditLogger:
    def __init__(self, config: HandsConfig) -> None:
        self._path = config.security.audit_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        line = json.dumps({"ts": time.time(), **event}, default=str)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def flush(self) -> None:
        return None   # writes are unbuffered per-line in M1
```

`src/hands/metrics.py`:

```python
"""In-process counters (DESIGN §4.22; histograms/OTLP land in M3)."""
from __future__ import annotations


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[tuple, int] = {}

    def inc(self, name: str, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        self._counters[key] = self._counters.get(key, 0) + 1

    def snapshot(self) -> dict[str, int]:
        return {f"{name}{dict(labels)}": v
                for (name, labels), v in self._counters.items()}
```

- [ ] **Step 7: Implement `src/hands/dispatcher.py`**

```python
"""The 7-phase pipeline (DESIGN §2.5). Every tool call flows through here."""
from __future__ import annotations

import time
import uuid
from typing import Any

import anyio
import structlog
from pydantic import BaseModel, ValidationError

from .audit import AuditLogger
from .config import HandsConfig
from .errors import (
    HandsError,
    InvalidArgsError,
    KillSwitchError,
    StaleScreenshotError,
)
from .metrics import Metrics
from .permissions import ActionDescriptor
from .registry import ToolRegistry, ToolSpec
from .retry import execute_with_retry
from .state import ActionRecord, StateManager

log = structlog.get_logger(__name__)


class Dispatcher:
    def __init__(self, registry: ToolRegistry, permissions: Any,
                 state: StateManager, audit: AuditLogger, metrics: Metrics,
                 config: HandsConfig) -> None:
        self._registry = registry
        self._permissions = permissions
        self._state = state
        self._audit = audit
        self._metrics = metrics
        self._config = config
        self._action_lock = anyio.Lock()  # HID is a global shared resource

    async def dispatch(self, tool_name: str, raw_args: dict[str, Any],
                       ctx: Any = None) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            spec = self._registry.get(tool_name)                 # phase 1
            args = self._validate(spec, raw_args)
            self._preflight(args)                                # phase 2
            self._permissions.authorize(ActionDescriptor(        # phase 3
                tool=spec.name,
                policy_class=spec.policy_class)).raise_if_denied()

            async def call() -> dict[str, Any]:
                return await spec.handler(args, ctx)

            if spec.policy_class == "read":                      # phases 4-5
                result = await execute_with_retry(call, spec.retry)
            else:
                async with self._action_lock:
                    result = await execute_with_retry(call, spec.retry)

            duration = time.monotonic() - started                # phase 6
            self._state.record_action(ActionRecord.ok(
                request_id, tool_name, args.model_dump(), duration))
            if spec.policy_class != "read":
                self._state.mark_screen_dirty()

            self._finish(request_id, tool_name, "ok")            # phase 7
            return {"ok": True, "request_id": request_id, **result}

        except HandsError as err:
            self._state.record_action(ActionRecord.failed(
                request_id, tool_name, raw_args or {}, err))
            self._finish(request_id, tool_name, err.code)
            return {"ok": False, "request_id": request_id,
                    "error": err.to_wire()}
        except Exception:
            log.exception("internal_error", tool=tool_name,
                          request_id=request_id)
            self._finish(request_id, tool_name, "INTERNAL")
            return {"ok": False, "request_id": request_id,
                    "error": {"code": "INTERNAL", "retryable": False,
                              "message": f"internal error {request_id}",
                              "remediation": None, "details": {}}}

    def _validate(self, spec: ToolSpec, raw_args: dict[str, Any]) -> BaseModel:
        try:
            return spec.args_model.model_validate(raw_args or {})
        except ValidationError as e:
            raise InvalidArgsError(
                f"invalid arguments for {spec.name}",
                details={"errors": e.errors(include_url=False)}) from None

    def _preflight(self, args: BaseModel) -> None:
        if self._config.security.kill_switch_engaged():
            raise KillSwitchError(
                "kill switch engaged",
                remediation=f"remove {self._config.security.kill_switch_path}")
        if not hasattr(args, "require_fresh_screenshot"):
            return
        required = args.require_fresh_screenshot
        if required is None:
            required = self._config.observe.require_fresh_default
        if not required:
            return
        meta = self._state.latest_screenshot_meta
        max_age = self._config.observe.max_screenshot_age_s
        if (meta is None or self._state.screen_dirty
                or time.monotonic() - meta["ts"] > max_age):
            raise StaleScreenshotError(
                "coordinate action requires a fresh screenshot",
                remediation="call the screenshot tool, then retry")

    def _finish(self, request_id: str, tool: str, outcome: str) -> None:
        self._audit.record({"request_id": request_id, "tool": tool,
                            "outcome": outcome})
        self._metrics.inc("tool_calls_total", tool=tool, outcome=outcome)
```

- [ ] **Step 8: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 14: Built-in MCP tools (`tools/`)

**Files:**
- Create: `src/hands/tools/__init__.py`, `src/hands/tools/pointer.py`, `src/hands/tools/typing.py`, `src/hands/tools/observe.py`
- Test: `tests/integration/test_tools.py`

**Interfaces:**
- Consumes: registry/dispatcher (13), services (10–12), state (8), types (3). Tool modules receive a `container` object with attributes `config, driver, state, mouse, keyboard, screenshots` (the real `Container` arrives in Task 15; tests use a `SimpleNamespace`).
- Produces: `register_builtin_tools(registry: ToolRegistry, container) -> None` registering exactly: `screenshot`, `get_state`, `wait`, `mouse_move`, `mouse_click`, `mouse_drag`, `mouse_scroll`, `keyboard_type`, `key_press`. Handler responses per DESIGN §5 (subset: no policy extras yet).

- [ ] **Step 1: Write failing tests** — `tests/integration/test_tools.py`:

```python
import base64
from types import SimpleNamespace

import pytest

from hands.audit import AuditLogger
from hands.config import HandsConfig
from hands.dispatcher import Dispatcher
from hands.metrics import Metrics
from hands.permissions import AllowAllPermissions
from hands.registry import ToolRegistry
from hands.services.coords import CoordinateMapper
from hands.services.keyboard import KeyboardService
from hands.services.mouse import MouseService
from hands.services.screenshot import ScreenshotService
from hands.state import StateManager
from hands.tools import register_builtin_tools

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"screenshot", "get_state", "wait", "mouse_move",
                  "mouse_click", "mouse_drag", "mouse_scroll",
                  "keyboard_type", "key_press"}


@pytest.fixture
def wired(fake_driver, tmp_path):
    cfg = HandsConfig()
    cfg.mouse.click_delay_ms = 0
    cfg.keyboard.chunk_delay_ms = 0
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    state = StateManager(cfg)
    coords = CoordinateMapper(fake_driver.displays())
    container = SimpleNamespace(
        config=cfg, driver=fake_driver, state=state,
        mouse=MouseService(fake_driver, coords, state, cfg),
        keyboard=KeyboardService(fake_driver, cfg),
        screenshots=ScreenshotService(fake_driver, state, cfg))
    reg = ToolRegistry()
    register_builtin_tools(reg, container)
    disp = Dispatcher(reg, AllowAllPermissions(), state,
                      AuditLogger(cfg), Metrics(), cfg)
    return disp, reg, fake_driver


def test_all_builtins_registered(wired):
    _, reg, _ = wired
    assert {s.name for s in reg.list_specs()} == EXPECTED_TOOLS


async def test_screenshot_tool_returns_png_and_meta(wired):
    disp, _, _ = wired
    res = await disp.dispatch("screenshot", {})
    assert res["ok"] is True
    assert base64.b64decode(res["image_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert res["bounds_pt"]["width"] == 1440
    assert res["px_per_pt"] > 0


async def test_click_tool_moves_and_reports_cursor(wired):
    disp, _, driver = wired
    res = await disp.dispatch("mouse_click", {"x": 10, "y": 20})
    assert res["ok"] is True
    assert res["cursor"] == {"x": 10, "y": 20}
    kinds = [ev.kind for _, ev in driver.pop_events()]
    assert kinds == ["move", "down", "up"]


async def test_keyboard_type_tool(wired):
    disp, _, driver = wired
    res = await disp.dispatch("keyboard_type", {"text": "hi there"})
    assert res["ok"] is True and res["chars_typed"] == 8
    assert driver.typed_text() == "hi there"


async def test_key_press_bad_chord_is_invalid_args(wired):
    disp, _, _ = wired
    res = await disp.dispatch("key_press", {"chord": "cmd+Retrun"})
    assert res["error"]["code"] == "INVALID_ARGS"


async def test_get_state_reports_cursor_and_displays(wired):
    disp, _, _ = wired
    await disp.dispatch("mouse_move", {"x": 7, "y": 9})
    res = await disp.dispatch("get_state", {"include_history": 5})
    assert res["cursor"] == {"x": 7, "y": 9}
    assert res["displays"][0]["bounds_pt"]["width"] == 1440
    assert res["history"][-1]["tool"] == "mouse_move"


async def test_wait_tool(wired):
    disp, _, _ = wired
    res = await disp.dispatch("wait", {"duration_ms": 10})
    assert res["ok"] is True and res["met"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.tools'`

- [ ] **Step 3: Implement `src/hands/tools/pointer.py`**

```python
"""Mouse tools. Thin: validate -> one service call -> shape result."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..errors import InvalidArgsError
from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import MODIFIER_NAMES, ModifierFlags, MouseButton, Point

_COORD_HELP = ("Coordinates are logical points, origin top-left of the main "
               "display. Compute them from the screenshot tool's bounds_pt "
               "and px_per_pt metadata.")


class MoveArgs(BaseModel, extra="forbid"):
    x: float
    y: float
    duration_ms: int = Field(default=0, ge=0, le=5000)
    clamp: bool = False
    require_fresh_screenshot: bool | None = None


class ClickArgs(BaseModel, extra="forbid"):
    x: float | None = None
    y: float | None = None
    button: MouseButton = MouseButton.LEFT
    count: int = Field(default=1, ge=1, le=3)
    modifiers: list[str] = []
    clamp: bool = False
    require_fresh_screenshot: bool | None = None


class PathPoint(BaseModel, extra="forbid"):
    x: float
    y: float


class DragArgs(BaseModel, extra="forbid"):
    path: list[PathPoint] = Field(min_length=2, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0, le=10000)
    button: MouseButton = MouseButton.LEFT
    require_fresh_screenshot: bool | None = None


class ScrollArgs(BaseModel, extra="forbid"):
    x: float | None = None
    y: float | None = None
    dx: int = Field(default=0, ge=-100, le=100)
    dy: int = Field(default=0, ge=-100, le=100)
    pixels: bool = False


def register(registry: ToolRegistry, container) -> None:
    mouse = container.mouse

    async def move(args: MoveArgs, ctx) -> dict:
        p = await mouse.move(Point(args.x, args.y), args.duration_ms,
                             clamp=args.clamp)
        return {"cursor": {"x": p.x, "y": p.y}}

    async def click(args: ClickArgs, ctx) -> dict:
        at = (Point(args.x, args.y)
              if args.x is not None and args.y is not None else None)
        res = await mouse.click(at, args.button, args.count,
                                _mods(args.modifiers), clamp=args.clamp)
        return {"cursor": {"x": res.cursor.x, "y": res.cursor.y},
                "screen_dirty": True}

    async def drag(args: DragArgs, ctx) -> dict:
        await mouse.drag([Point(p.x, p.y) for p in args.path],
                         args.duration_ms, args.button)
        return {"screen_dirty": True}

    async def scroll(args: ScrollArgs, ctx) -> dict:
        at = (Point(args.x, args.y)
              if args.x is not None and args.y is not None else None)
        await mouse.scroll(at, args.dx, args.dy, args.pixels)
        return {"screen_dirty": True}

    registry.register(ToolSpec(
        "mouse_move", f"Move the mouse cursor. {_COORD_HELP}",
        MoveArgs, move, "act", RetryPolicy.pre_side_effect(),
        idempotent=True))
    registry.register(ToolSpec(
        "mouse_click",
        f"Click at (x, y), or at the current cursor if omitted. "
        f"{_COORD_HELP} After clicking, take a screenshot to verify the "
        f"result.", ClickArgs, click, "act", RetryPolicy.pre_side_effect()))
    registry.register(ToolSpec(
        "mouse_drag",
        f"Press, drag along path, release. {_COORD_HELP}",
        DragArgs, drag, "act", RetryPolicy.pre_side_effect()))
    registry.register(ToolSpec(
        "mouse_scroll",
        "Scroll at (x, y) (moves there first) or at the current cursor. "
        "Positive dy scrolls up, negative down, in wheel ticks unless "
        "pixels=true.", ScrollArgs, scroll, "act",
        RetryPolicy.pre_side_effect()))


def _mods(names: list[str]) -> ModifierFlags:
    flags = ModifierFlags.NONE
    for n in names:
        flag = MODIFIER_NAMES.get(n.lower())
        if flag is None:
            raise InvalidArgsError(f"unknown modifier {n!r}",
                                   details={"known": sorted(MODIFIER_NAMES)})
        flags |= flag
    return flags
```

- [ ] **Step 4: Implement `src/hands/tools/typing.py`**

```python
"""Keyboard tools."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import KeyChord


class TypeArgs(BaseModel, extra="forbid"):
    text: str = Field(min_length=1, max_length=10_000)
    chunk_delay_ms: int | None = Field(default=None, ge=0, le=1000)


class PressArgs(BaseModel, extra="forbid"):
    chord: str = Field(min_length=1, max_length=64)
    repeat: int = Field(default=1, ge=1, le=50)


def register(registry: ToolRegistry, container) -> None:
    keyboard = container.keyboard

    async def type_text(args: TypeArgs, ctx) -> dict:
        n = await keyboard.type_text(args.text, args.chunk_delay_ms)
        return {"chars_typed": n, "screen_dirty": True}

    async def press(args: PressArgs, ctx) -> dict:
        chord = KeyChord.parse(args.chord)   # raises InvalidArgsError
        await keyboard.press(chord, args.repeat)
        return {"screen_dirty": True}

    registry.register(ToolSpec(
        "keyboard_type",
        "Type text into the focused element using layout-independent "
        "unicode injection. Click the target field first.",
        TypeArgs, type_text, "act", RetryPolicy.pre_side_effect()))
    registry.register(ToolSpec(
        "key_press",
        "Press a key or shortcut chord, e.g. 'Return', 'cmd+s', "
        "'cmd+shift+p', 'F5'. Use keyboard_type for regular text.",
        PressArgs, press, "act", RetryPolicy.pre_side_effect()))
```

- [ ] **Step 5: Implement `src/hands/tools/observe.py`**

```python
"""Observation tools: screenshot, get_state, wait (duration-only in M1)."""
from __future__ import annotations

import base64
import dataclasses
from typing import Literal

import anyio
from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import Region


class RegionArg(BaseModel, extra="forbid"):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ScreenshotArgs(BaseModel, extra="forbid"):
    region: RegionArg | None = None
    format: Literal["png", "jpeg"] = "png"
    max_dim: int | None = Field(default=None, ge=64, le=4096)
    fresh: bool = False


class GetStateArgs(BaseModel, extra="forbid"):
    include_history: int = Field(default=0, ge=0, le=50)


class WaitArgs(BaseModel, extra="forbid"):
    duration_ms: int = Field(ge=0, le=60_000)


def register(registry: ToolRegistry, container) -> None:
    shots = container.screenshots
    state = container.state
    driver = container.driver
    config = container.config

    async def screenshot(args: ScreenshotArgs, ctx) -> dict:
        region = (Region(**args.region.model_dump())
                  if args.region else None)
        shot = await shots.capture(region, fmt=args.format,
                                   max_dim=args.max_dim, fresh=args.fresh)
        return {"image_b64": base64.b64encode(shot.data).decode(),
                **shot.meta()}

    async def get_state(args: GetStateArgs, ctx) -> dict:
        cur = driver.cursor_position()
        return {
            "cursor": {"x": cur.x, "y": cur.y},
            "displays": [dataclasses.asdict(d) for d in driver.displays()],
            "latest_screenshot": state.latest_screenshot_meta,
            "screen_dirty": state.screen_dirty,
            "kill_switch": config.security.kill_switch_engaged(),
            "history": [dataclasses.asdict(r)
                        for r in state.history(args.include_history)]
            if args.include_history else [],
        }

    async def wait(args: WaitArgs, ctx) -> dict:
        await anyio.sleep(args.duration_ms / 1000)
        return {"met": True, "waited_ms": args.duration_ms}

    registry.register(ToolSpec(
        "screenshot",
        "Capture the screen (or a region, in points). The response includes "
        "bounds_pt and px_per_pt: point = bounds_pt.origin + pixel / "
        "px_per_pt. Take a screenshot before any coordinate action.",
        ScreenshotArgs, screenshot, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "get_state",
        "Re-orientation: cursor position, displays, last screenshot "
        "metadata, kill-switch status, and recent action history.",
        GetStateArgs, get_state, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "wait",
        "Sleep for duration_ms (max 60000). Condition-based waits "
        "(window_present, text_present, screen_stable) arrive in M2.",
        WaitArgs, wait, "read", RetryPolicy.none(), idempotent=True))
```

- [ ] **Step 6: Implement `src/hands/tools/__init__.py`**

```python
from ..registry import ToolRegistry
from . import observe, pointer
from . import typing as typing_tools


def register_builtin_tools(registry: ToolRegistry, container) -> None:
    pointer.register(registry, container)
    typing_tools.register(registry, container)
    observe.register(registry, container)
```

- [ ] **Step 7: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 15: Container, MCP server, CLI, and end-to-end smoke test

**Files:**
- Create: `src/hands/container.py`, `src/hands/server.py`, `src/hands/cli.py`, `src/hands/__main__.py`
- Test: `tests/integration/test_server_e2e.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Container.build(config: HandsConfig) -> Container` with attributes `config, driver, state, coords, screenshots, mouse, keyboard, audit, metrics, permissions, registry, dispatcher`.
  - `build_server(container) -> mcp.server.lowlevel.Server`; `async run_server(config) -> None` (stdio loop, `keyboard.release_all()` in `finally`).
  - `main(argv=None) -> int` CLI: `hands serve` (default) and `hands doctor`.

- [ ] **Step 1: Write failing e2e tests** — `tests/integration/test_server_e2e.py`:

```python
import json

import pytest
from mcp.shared.memory import (
    create_connected_server_and_client_session as connect,
)

from hands.config import HandsConfig
from hands.container import Container
from hands.server import build_server

pytestmark = pytest.mark.anyio

# NOTE: if your installed mcp SDK version moved this helper, check
# `python -c "import mcp.shared.memory as m; print(dir(m))"` — it is the
# in-memory client<->server pair the SDK's own tests use.


@pytest.fixture
def server(tmp_path):
    cfg = HandsConfig(driver="fake")
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    cfg.mouse.click_delay_ms = 0
    return build_server(Container.build(cfg))


async def test_lists_all_builtin_tools(server):
    async with connect(server) as client:
        tools = (await client.list_tools()).tools
        names = {t.name for t in tools}
        assert {"screenshot", "get_state", "wait", "mouse_move",
                "mouse_click", "mouse_drag", "mouse_scroll",
                "keyboard_type", "key_press"} <= names


async def test_screenshot_returns_image_block_plus_json(server):
    async with connect(server) as client:
        res = await client.call_tool("screenshot", {})
        kinds = [c.type for c in res.content]
        assert "image" in kinds and "text" in kinds
        meta = json.loads(
            next(c.text for c in res.content if c.type == "text"))
        assert meta["ok"] is True
        assert meta["px_per_pt"] > 0
        assert "image_b64" not in meta   # pixels live in the image block


async def test_click_roundtrip(server):
    async with connect(server) as client:
        res = await client.call_tool("mouse_click", {"x": 10, "y": 20})
        payload = json.loads(res.content[-1].text)
        assert payload["ok"] is True
        assert payload["cursor"] == {"x": 10, "y": 20}


async def test_error_envelope_over_the_wire(server):
    async with connect(server) as client:
        res = await client.call_tool("mouse_click", {"x": 1e9, "y": 1e9})
        payload = json.loads(res.content[-1].text)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGS"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_server_e2e.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.container'`

- [ ] **Step 3: Implement `src/hands/container.py`**

```python
"""Composition root (DESIGN §3.1). Builds every service exactly once."""
from __future__ import annotations

import sys

from .audit import AuditLogger
from .config import HandsConfig
from .dispatcher import Dispatcher
from .driver.base import Driver
from .metrics import Metrics
from .permissions import AllowAllPermissions
from .registry import ToolRegistry
from .services.coords import CoordinateMapper
from .services.keyboard import KeyboardService
from .services.mouse import MouseService
from .services.screenshot import ScreenshotService
from .state import StateManager
from .tools import register_builtin_tools


def _make_driver(config: HandsConfig) -> Driver:
    choice = config.driver
    if choice == "auto":
        choice = "macos" if sys.platform == "darwin" else "fake"
    if choice == "fake":
        from .driver.fake import FakeDriver
        return FakeDriver()
    from .driver.macos import MacOSDriver
    return MacOSDriver()


class Container:
    @classmethod
    def build(cls, config: HandsConfig) -> "Container":
        self = cls()
        self.config = config
        self.driver = _make_driver(config)
        self.state = StateManager(config)
        self.coords = CoordinateMapper(self.driver.displays())
        self.screenshots = ScreenshotService(self.driver, self.state, config)
        self.mouse = MouseService(self.driver, self.coords, self.state,
                                  config)
        self.keyboard = KeyboardService(self.driver, config)
        self.audit = AuditLogger(config)
        self.metrics = Metrics()
        self.permissions = AllowAllPermissions()
        self.registry = ToolRegistry()
        register_builtin_tools(self.registry, self)
        self.dispatcher = Dispatcher(self.registry, self.permissions,
                                     self.state, self.audit, self.metrics,
                                     config)
        return self
```

- [ ] **Step 4: Implement `src/hands/server.py`**

```python
"""MCP server assembly over the low-level SDK Server (plan: Global
Constraints, FastMCP deviation note)."""
from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import HandsConfig
from .container import Container


def build_server(container: Container) -> Server:
    server = Server("hands")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=s.name, description=s.description,
                           inputSchema=s.args_model.model_json_schema())
                for s in container.registry.list_specs()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None
                        ) -> list[types.TextContent | types.ImageContent]:
        result = await container.dispatcher.dispatch(name, arguments or {})
        blocks: list[types.TextContent | types.ImageContent] = []
        image_b64 = result.pop("image_b64", None)
        if image_b64:
            mime = f"image/{result.get('fmt', 'png')}"
            blocks.append(types.ImageContent(type="image", data=image_b64,
                                             mimeType=mime))
        blocks.append(types.TextContent(type="text",
                                        text=json.dumps(result)))
        return blocks

    return server


async def run_server(config: HandsConfig) -> None:
    container = Container.build(config)
    server = build_server(container)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())
    finally:
        # A crash mid-hotkey must not leave cmd held down (DESIGN §2.6).
        container.keyboard.release_all()
```

- [ ] **Step 5: Implement `src/hands/cli.py` and `src/hands/__main__.py`**

`src/hands/cli.py`:

```python
"""CLI: `hands serve` (default) and `hands doctor`."""
from __future__ import annotations

import argparse
import dataclasses
import json

import anyio

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hands")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio")
    sub.add_parser("doctor", help="print resolved config and driver status")
    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "doctor":
        from .container import Container
        c = Container.build(config)
        info = {
            "config": config.model_dump(mode="json"),
            "driver": type(c.driver).__name__,
            "displays": [dataclasses.asdict(d) for d in c.driver.displays()],
            "tools": sorted(s.name for s in c.registry.list_specs()),
        }
        print(json.dumps(info, indent=2, default=str))
        return 0

    from .server import run_server
    anyio.run(run_server, config)
    return 0
```

`src/hands/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 6: Run the e2e tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Manual smoke test**

Run: `HANDS_DRIVER=fake uv run hands doctor`
Expected: JSON showing `"driver": "FakeDriver"`, the 1440×900 display, and all 9 tool names.

Run: `printf '' | HANDS_DRIVER=fake uv run hands serve; echo "exit=$?"`
Expected: the server starts, reads EOF on stdin, and exits cleanly with exit=0 and no traceback.

---

### Task 16: macOS driver and contract suite

**Files:**
- Create: `src/hands/driver/macos.py`, `tests/contract/test_driver_contract.py` (create `tests/contract/` dir)

**Interfaces:**
- Consumes: `Driver` protocol, `RawFrame`, `MouseEventSpec` (Task 6), types (3), errors (2).
- Produces: `MacOSDriver()` implementing the full M1 `Driver` protocol. Capture via `/usr/sbin/screencapture` CLI (ScreenCaptureKit lands in M2); events via Quartz `CGEventPost` to `kCGHIDEventTap`; text via `CGEventKeyboardSetUnicodeString`.

- [ ] **Step 1: Write the contract suite** — `tests/contract/test_driver_contract.py`:

```python
"""Same assertions against fake and (opt-in) real driver (DESIGN §12).

The real-driver leg is READ-ONLY: it captures and reads, never posts
events. Opt in on macOS (needs Screen Recording permission) with:
    HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q
"""
import os
import sys

import pytest

from hands.driver.base import Driver
from hands.driver.fake import FakeDriver


def _params() -> list[str]:
    params = ["fake"]
    if (sys.platform == "darwin"
            and os.environ.get("HANDS_CONTRACT_MACOS") == "1"):
        params.append("macos")
    return params


@pytest.fixture(params=_params())
def driver(request) -> Driver:
    if request.param == "fake":
        return FakeDriver()
    from hands.driver.macos import MacOSDriver
    return MacOSDriver()


def test_satisfies_protocol(driver):
    assert isinstance(driver, Driver)


def test_exactly_one_main_display(driver):
    mains = [d for d in driver.displays() if d.is_main]
    assert len(mains) == 1
    assert mains[0].scale >= 1.0
    assert mains[0].bounds_pt.width > 0


def test_full_capture_geometry(driver):
    d = next(x for x in driver.displays() if x.is_main)
    frame = driver.capture(None, None)
    assert frame.bounds_pt == d.bounds_pt
    # encoded pixel width must match bounds * px_per_pt (±2 px rounding)
    assert abs(frame.image.width
               - frame.bounds_pt.width * frame.px_per_pt) <= 2


def test_region_capture_geometry(driver):
    from hands.types import Region
    region = Region(10, 10, 200, 100)
    frame = driver.capture(region, None)
    assert frame.bounds_pt == region
    assert abs(frame.image.width - 200 * frame.px_per_pt) <= 2


def test_cursor_position_within_a_display(driver):
    p = driver.cursor_position()
    assert any(d.bounds_pt.contains(p) for d in driver.displays())
```

- [ ] **Step 2: Run to verify the fake leg passes and macos leg fails**

Run: `uv run pytest tests/contract -q`
Expected: fake leg PASSES already (5 passed). Then on macOS:
Run: `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hands.driver.macos'`

- [ ] **Step 3: Implement** — `src/hands/driver/macos.py`:

```python
"""Real macOS driver: Quartz events + screencapture CLI (DESIGN §3.1).

M1 captures via /usr/sbin/screencapture — slower (~150 ms) than
ScreenCaptureKit but dependency-light and reliable; SCK lands in M2.
Requires TCC grants: Screen Recording (capture) and Accessibility
(event posting).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import Quartz
from PIL import Image

from ..errors import DriverError, PermissionMissingError
from ..types import (
    DisplayInfo,
    ModifierFlags,
    MouseButton,
    Point,
    Region,
)
from .base import MouseEventSpec, RawFrame

_FLAG_MASKS = {
    ModifierFlags.CMD: Quartz.kCGEventFlagMaskCommand,
    ModifierFlags.SHIFT: Quartz.kCGEventFlagMaskShift,
    ModifierFlags.ALT: Quartz.kCGEventFlagMaskAlternate,
    ModifierFlags.CTRL: Quartz.kCGEventFlagMaskControl,
}

_CG_BUTTONS = {
    MouseButton.LEFT: Quartz.kCGMouseButtonLeft,
    MouseButton.RIGHT: Quartz.kCGMouseButtonRight,
    MouseButton.MIDDLE: Quartz.kCGMouseButtonCenter,
}

_DOWN = {MouseButton.LEFT: Quartz.kCGEventLeftMouseDown,
         MouseButton.RIGHT: Quartz.kCGEventRightMouseDown,
         MouseButton.MIDDLE: Quartz.kCGEventOtherMouseDown}
_UP = {MouseButton.LEFT: Quartz.kCGEventLeftMouseUp,
       MouseButton.RIGHT: Quartz.kCGEventRightMouseUp,
       MouseButton.MIDDLE: Quartz.kCGEventOtherMouseUp}
_DRAG = {MouseButton.LEFT: Quartz.kCGEventLeftMouseDragged,
         MouseButton.RIGHT: Quartz.kCGEventRightMouseDragged,
         MouseButton.MIDDLE: Quartz.kCGEventOtherMouseDragged}


def _cg_flags(mods: ModifierFlags) -> int:
    flags = 0
    for flag, mask in _FLAG_MASKS.items():
        if flag in mods:
            flags |= mask
    return flags


class MacOSDriver:
    def __init__(self) -> None:
        self._pressed: set[MouseButton] = set()

    # --- perception ---------------------------------------------------------
    def displays(self) -> list[DisplayInfo]:
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        if err != 0:
            raise DriverError(f"CGGetActiveDisplayList failed: {err}")
        main_id = Quartz.CGMainDisplayID()
        out: list[DisplayInfo] = []
        for did in ids[:count]:
            b = Quartz.CGDisplayBounds(did)
            scale = Quartz.CGDisplayPixelsWide(did) / b.size.width
            out.append(DisplayInfo(
                display_id=int(did),
                bounds_pt=Region(b.origin.x, b.origin.y,
                                 b.size.width, b.size.height),
                scale=float(scale),
                is_main=(did == main_id)))
        return out

    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame:
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionMissingError(
                "Screen Recording permission is not granted",
                remediation=("Enable this app under System Settings > "
                             "Privacy & Security > Screen Recording, "
                             "then restart the server"))
        main = next(d for d in self.displays() if d.is_main)
        bounds = region if region is not None else main.bounds_pt
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "shot.png"
            cmd = ["/usr/sbin/screencapture", "-x"]
            if region is not None:
                cmd += ["-R", f"{region.x},{region.y},"
                              f"{region.width},{region.height}"]
            cmd.append(str(path))
            proc = subprocess.run(cmd, capture_output=True, timeout=10)
            if proc.returncode != 0 or not path.exists():
                raise DriverError(
                    "screencapture failed",
                    details={"stderr": proc.stderr.decode().strip()})
            img = Image.open(path)
            img.load()   # read before the temp dir vanishes
        return RawFrame(img, bounds, img.width / bounds.width,
                        main.display_id)

    def cursor_position(self) -> Point:
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return Point(loc.x, loc.y)

    # --- input --------------------------------------------------------------
    def post_mouse(self, event: MouseEventSpec) -> None:
        if event.kind == "down":
            etype = _DOWN[event.button]
        elif event.kind == "up":
            etype = _UP[event.button]
        elif self._pressed:
            etype = _DRAG[next(iter(self._pressed))]
        else:
            etype = Quartz.kCGEventMouseMoved
        cg = Quartz.CGEventCreateMouseEvent(
            None, etype, (event.at.x, event.at.y), _CG_BUTTONS[event.button])
        if cg is None:
            raise DriverError("CGEventCreateMouseEvent returned None")
        if event.kind in ("down", "up"):
            Quartz.CGEventSetIntegerValueField(
                cg, Quartz.kCGMouseEventClickState, event.click_count)
        flags = _cg_flags(event.modifiers)
        if flags:
            Quartz.CGEventSetFlags(cg, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)
        if event.kind == "down":
            self._pressed.add(event.button)
        elif event.kind == "up":
            self._pressed.discard(event.button)

    def post_scroll(self, at: Point, dx: int, dy: int,
                    pixels: bool) -> None:
        unit = (Quartz.kCGScrollEventUnitPixel if pixels
                else Quartz.kCGScrollEventUnitLine)
        cg = Quartz.CGEventCreateScrollWheelEvent(None, unit, 2, dy, dx)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    def type_unicode(self, text: str) -> None:
        # Layout-independent unicode injection (DESIGN §4.6).
        for down in (True, False):
            cg = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(cg, len(text), text)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None:
        cg = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        mask = _cg_flags(flags)
        if mask and down:
            Quartz.CGEventSetFlags(cg, mask)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)
```

- [ ] **Step 4: Verify on macOS**

Run: `uv sync --all-extras && HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q`
Expected: 10 passed (5 per driver leg). If the macos leg fails with `PERMISSION_MISSING`, grant Screen Recording to your terminal in System Settings and re-run.

Run: `uv run pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Manual verification against the real desktop**

Run: `HANDS_DRIVER=macos uv run hands doctor`
Expected: `"driver": "MacOSDriver"` with your real display bounds and scale (2.0 on Retina).

Optional live check (moves your real cursor — be ready):
Run: `HANDS_DRIVER=macos uv run python -c "
import anyio
from hands.config import load_config
from hands.container import Container
from hands.types import Point


async def main():
    c = Container.build(load_config())
    await c.mouse.move(Point(200, 200), duration_ms=500)
    print('cursor now:', c.driver.cursor_position())

anyio.run(main)"`
Expected: the cursor glides to (200, 200) and the printed position matches (±1 pt).

---

## Plan completion criteria

- `uv run pytest -q` green on any OS (fake driver).
- `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q` green on macOS with permissions granted.
- `hands doctor` reports config, driver, displays, and 9 tools.
- An MCP client connecting over stdio can list tools, take a screenshot, click, and type.
- Nothing committed to git (user instruction); working tree contains all new files ready for review.
