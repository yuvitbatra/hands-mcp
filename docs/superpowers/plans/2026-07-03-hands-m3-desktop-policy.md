# Hands Milestone 3 — Desktop Control & Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full desktop control (clipboard, windows, apps, AX tree) and the real security layer: rule-based `PermissionEngine` with confirmation hooks, deny-lists, rate limiting, secure-input refusal, hash-chained audit, and richer metrics/CLI diagnostics.

**Architecture:** Extends the M1/M2 layers: the `Driver` protocol grows clipboard/window/app/AX/permission surfaces (fake first, macOS last), three new services (`ClipboardService`, `WindowService`, `AppService`) follow the M1 service pattern, and the M1 policy/audit/metrics stubs are replaced behind their existing interfaces (`authorize(action)`, `record(event)`, `inc(...)`) so the dispatcher barely changes.

**Tech Stack:** Same as M1/M2. macOS additions: `pyobjc-framework-Cocoa` (AppKit: NSWorkspace, NSPasteboard, NSRunningApplication), `pyobjc-framework-ApplicationServices` (AXUIElement).

## Milestone map (context, not tasks)

- **M1 (done):** core framework, 9 tools, fake + macOS driver v1.
- **M2 (done, prerequisite):** OCR, waiter, verification, `find_text`/`verify`/condition `wait` (11 tools).
- **M3 (this plan):** clipboard/windows/apps/AX services + 10 new tools (21 total), `PermissionEngine` + confirmation, hash-chained audit, metrics histograms, `hands permissions` / `hands audit verify` / `doctor --metrics`.
- **M4 (future plan):** plugin system, `execute_sequence`, e2e fixture app, perf/stress suites.

## Global Constraints

- **M1 and M2 plans must be fully implemented and green (`uv run pytest -q`) before starting.**
- Python `>=3.12`; `src/` layout; package `hands`; managed with `uv`.
- **No git commits for now (user instruction, 2026-07-03).** Tasks end with a "Verify" step running the full test suite instead of a commit. When the user lifts this, commit once per completed task with `feat:`/`test:` prefixes.
- All coordinates are **logical points, top-left origin of the main display, y-down** (DESIGN §4.12).
- `stdout` is reserved for the MCP transport in `serve` mode. CLI subcommands (`doctor`, `permissions`, `audit`) may print to stdout — they never run concurrently with the transport.
- Use `anyio`; blocking pyobjc/AppKit/AX calls run via `anyio.to_thread.run_sync`.
- Pydantic argument models use `extra="forbid"`.
- Error codes unchanged (M1 list). `PermissionMissingError` (OS/TCC) vs `PolicyDeniedError` (agent policy) stay strictly separate (DESIGN §4.19).
- Policy classes: `read` (allow), `act` (allow, rate-limited), `sensitive` (confirm by default). `clipboard_get` is **sensitive**; `window_manage(action="close")` and `app_close(force=true)` escalate to sensitive (DESIGN §13.3).
- Redaction invariant (DESIGN §8.2/§13.6): clipboard content and typed text never enter state, audit, logs, or metrics — length/hash only.
- macOS-only tests gated by `HANDS_CONTRACT_MACOS=1` as in M1/M2.

---

### Task 1: Clipboard and secure-input driver surface

**Files:**
- Modify: `src/hands/types.py` (append `ClipboardContent`)
- Modify: `src/hands/driver/base.py` (protocol additions)
- Modify: `src/hands/driver/fake.py` (fake clipboard + secure input)
- Modify: `src/hands/config.py` (add `clipboard` section)
- Test: `tests/unit/test_fake_driver.py` (append), `tests/unit/test_config.py` (append)

**Interfaces:**
- Consumes: M1 driver module (`_maybe_fail` pattern), M1 config style.
- Produces:
  - `ClipboardContent(kind: Literal["text", "image", "empty"], text: str | None = None, image_png: bytes | None = None)` frozen dataclass in `hands.types`.
  - `Driver` protocol additions: `clipboard_read() -> ClipboardContent`, `clipboard_write(content: ClipboardContent) -> None`, `secure_input_active() -> bool`.
  - `FakeDriver` additions: in-memory clipboard (starts `empty`), `set_secure_input(active: bool)` test helper, `clipboard_read`/`clipboard_write` participate in `fail_next`.
  - Config: `HandsConfig.clipboard: ClipboardConfig(restore_delay_ms: int = 500)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_fake_driver.py`:

```python
from hands.types import ClipboardContent


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
```

Append to `tests/unit/test_config.py`:

```python
def test_m3_clipboard_config():
    cfg = HandsConfig()
    assert cfg.clipboard.restore_delay_ms == 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_config.py -q`
Expected: FAIL — `ImportError: ClipboardContent`.

- [ ] **Step 3: Implement**

Append to `src/hands/types.py` (add `Literal` to the `typing` import):

```python
@dataclass(frozen=True, slots=True)
class ClipboardContent:
    """Clipboard payload. Sensitive by policy (DESIGN §4.7): never logged,
    never stored in state — only hashes/lengths may leave this object."""
    kind: Literal["text", "image", "empty"]
    text: str | None = None
    image_png: bytes | None = None
```

Add to the `Driver` protocol in `src/hands/driver/base.py`:

```python
    def clipboard_read(self) -> ClipboardContent: ...
    def clipboard_write(self, content: ClipboardContent) -> None: ...
    def secure_input_active(self) -> bool: ...
```

(import `ClipboardContent` from `..types`.)

In `src/hands/driver/fake.py` add to `__init__`:

```python
        self._clipboard = ClipboardContent("empty")
        self._secure_input = False
```

and methods:

```python
    def set_secure_input(self, active: bool) -> None:
        self._secure_input = active

    def clipboard_read(self) -> ClipboardContent:
        self._maybe_fail("clipboard_read")
        return self._clipboard

    def clipboard_write(self, content: ClipboardContent) -> None:
        self._maybe_fail("clipboard_write")
        self._clipboard = content
        self.events.append(("clipboard_write", content.kind))

    def secure_input_active(self) -> bool:
        return self._secure_input
```

In `src/hands/config.py` add:

```python
class ClipboardConfig(BaseModel):
    restore_delay_ms: int = 500
```

and on `HandsConfig`:

```python
    clipboard: ClipboardConfig = ClipboardConfig()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 2: Clipboard service and tools

**Files:**
- Create: `src/hands/services/clipboard.py`, `src/hands/tools/clipboard.py`
- Modify: `src/hands/container.py`, `src/hands/tools/__init__.py`
- Test: `tests/unit/test_clipboard_service.py`, `tests/unit/test_tools_clipboard.py`

**Interfaces:**
- Consumes: `ClipboardContent`, driver surface (Task 1), `KeyboardService.press` + `KeyChord.parse` (M1), `PolicyDeniedError` (M1).
- Produces:
  - `ClipboardService(driver, keyboard, config)` with:
    - `async get(fmt: Literal["text", "image", "any"] = "any") -> ClipboardContent` — raises `PolicyDeniedError` when `driver.secure_input_active()` (a password field is focused); returns `kind="empty"` when the requested format isn't present.
    - `async set(content: ClipboardContent) -> None`
    - `async paste(text: str, restore: bool = True) -> None` — save → set → ⌘V → sleep `restore_delay_ms` → restore (DESIGN §4.7).
  - `Container.clipboard: ClipboardService` built after `self.keyboard`.
  - Tools: `clipboard_get {format?}` (**sensitive**, R:read, I:yes) → `{ok, kind, text?, image_b64?}`; `clipboard_set {text | image_b64}` (act, R:pre, I:yes) → `{ok}`; `clipboard_paste {text, restore=true}` (act, R:pre, I:no) → `{ok}`.

- [ ] **Step 1: Write the failing service tests** — `tests/unit/test_clipboard_service.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.errors import PolicyDeniedError
from hands.services.clipboard import ClipboardService
from hands.services.keyboard import KeyboardService
from hands.types import ClipboardContent

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    cfg = HandsConfig()
    cfg.clipboard.restore_delay_ms = 0
    return ClipboardService(fake_driver,
                            KeyboardService(fake_driver, cfg), cfg)


async def test_set_get_round_trip(service):
    await service.set(ClipboardContent("text", text="hi"))
    got = await service.get()
    assert got.kind == "text" and got.text == "hi"


async def test_get_wrong_format_is_empty(service):
    await service.set(ClipboardContent("text", text="hi"))
    assert (await service.get("image")).kind == "empty"


async def test_get_refuses_during_secure_input(fake_driver, service):
    fake_driver.set_secure_input(True)
    with pytest.raises(PolicyDeniedError):
        await service.get()


async def test_paste_sets_presses_cmd_v_and_restores(fake_driver, service):
    await service.set(ClipboardContent("text", text="original"))
    fake_driver.pop_events()
    await service.paste("pasted")
    events = fake_driver.pop_events()
    kinds = [e[0] for e in events]
    # write(pasted), key events for cmd+v, write(original) — in that order.
    assert kinds[0] == "clipboard_write"
    assert "key" in kinds
    assert kinds[-1] == "clipboard_write"
    assert (await service.get()).text == "original"


async def test_paste_no_restore(fake_driver, service):
    await service.set(ClipboardContent("text", text="original"))
    await service.paste("pasted", restore=False)
    assert (await service.get()).text == "pasted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_clipboard_service.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.clipboard`.

- [ ] **Step 3: Implement `src/hands/services/clipboard.py`**

```python
"""Clipboard service (DESIGN §4.7). Restore-after-paste is on by default:
agents must not destroy the user's clipboard."""
from __future__ import annotations

from typing import Literal

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import PolicyDeniedError
from ..types import ClipboardContent, KeyChord
from .keyboard import KeyboardService


class ClipboardService:
    def __init__(self, driver: Driver, keyboard: KeyboardService,
                 config: HandsConfig) -> None:
        self._driver = driver
        self._keyboard = keyboard
        self._cfg = config.clipboard

    async def get(self, fmt: Literal["text", "image", "any"] = "any"
                  ) -> ClipboardContent:
        if await anyio.to_thread.run_sync(self._driver.secure_input_active):
            raise PolicyDeniedError(
                "secure text entry is active (a password field is focused); "
                "clipboard reads are refused (DESIGN §13.5)")
        content = await anyio.to_thread.run_sync(self._driver.clipboard_read)
        if fmt != "any" and content.kind != fmt:
            return ClipboardContent("empty")
        return content

    async def set(self, content: ClipboardContent) -> None:
        await anyio.to_thread.run_sync(self._driver.clipboard_write, content)

    async def paste(self, text: str, restore: bool = True) -> None:
        saved = await anyio.to_thread.run_sync(self._driver.clipboard_read)
        await self.set(ClipboardContent("text", text=text))
        await self._keyboard.press(KeyChord.parse("cmd+v"))
        if restore:
            await anyio.sleep(self._cfg.restore_delay_ms / 1000)
            await self.set(saved)
```

- [ ] **Step 4: Run service tests to verify they pass**

Run: `uv run pytest tests/unit/test_clipboard_service.py -q`
Expected: 5 passed.

- [ ] **Step 5: Write the failing tool tests** — `tests/unit/test_tools_clipboard.py`:

```python
import base64
from types import SimpleNamespace

import pytest

from hands.config import HandsConfig
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.clipboard import ClipboardService
from hands.services.keyboard import KeyboardService
from hands.tools import clipboard as clipboard_tools
from hands.types import ClipboardContent

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    cfg.clipboard.restore_delay_ms = 0
    driver = FakeDriver()
    keyboard = KeyboardService(driver, cfg)
    clip = ClipboardService(driver, keyboard, cfg)
    container = SimpleNamespace(config=cfg, clipboard=clip)
    reg = ToolRegistry()
    clipboard_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg, clip=clip)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_clipboard_get_is_sensitive_read(env):
    spec = env.registry.get("clipboard_get")
    assert spec.policy_class == "sensitive"
    assert spec.idempotent is True


async def test_set_then_get_text(env):
    await _call(env, "clipboard_set", {"text": "abc"})
    res = await _call(env, "clipboard_get", {})
    assert res["kind"] == "text" and res["text"] == "abc"


async def test_set_image_b64(env):
    png = base64.b64encode(b"\x89PNG fake").decode()
    await _call(env, "clipboard_set", {"image_b64": png})
    res = await _call(env, "clipboard_get", {"format": "image"})
    assert res["kind"] == "image"
    assert base64.b64decode(res["image_b64"]) == b"\x89PNG fake"


async def test_set_requires_exactly_one_payload(env):
    from pydantic import ValidationError
    spec = env.registry.get("clipboard_set")
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({})
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({"text": "a", "image_b64": "Yg=="})


async def test_paste_tool(env):
    await _call(env, "clipboard_set", {"text": "keep me"})
    res = await _call(env, "clipboard_paste", {"text": "insert this"})
    assert res == {}
    assert (await env.clip.get()).text == "keep me"
```

Run: `uv run pytest tests/unit/test_tools_clipboard.py -q`
Expected: FAIL — `ImportError: cannot import name 'clipboard' from 'hands.tools'`.

- [ ] **Step 6: Implement `src/hands/tools/clipboard.py`**

```python
"""MCP clipboard tools (DESIGN §5.8–5.9)."""
from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import ClipboardContent


class ClipboardGetArgs(BaseModel, extra="forbid"):
    format: Literal["text", "image", "any"] = "any"


class ClipboardSetArgs(BaseModel, extra="forbid"):
    text: str | None = Field(default=None, max_length=100_000)
    image_b64: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.text is None) == (self.image_b64 is None):
            raise ValueError("provide exactly one of text or image_b64")
        return self


class ClipboardPasteArgs(BaseModel, extra="forbid"):
    text: str = Field(max_length=100_000)
    restore: bool = True


def register(registry: ToolRegistry, container) -> None:
    clip = container.clipboard

    async def clipboard_get(args: ClipboardGetArgs, ctx) -> dict:
        content = await clip.get(args.format)
        out: dict = {"kind": content.kind}
        if content.text is not None:
            out["text"] = content.text
        if content.image_png is not None:
            out["image_b64"] = base64.b64encode(
                content.image_png).decode()
        return out

    async def clipboard_set(args: ClipboardSetArgs, ctx) -> dict:
        if args.text is not None:
            await clip.set(ClipboardContent("text", text=args.text))
        else:
            await clip.set(ClipboardContent(
                "image", image_png=base64.b64decode(args.image_b64)))
        return {}

    async def clipboard_paste(args: ClipboardPasteArgs, ctx) -> dict:
        await clip.paste(args.text, args.restore)
        return {}

    registry.register(ToolSpec(
        "clipboard_get",
        "Read the clipboard (sensitive: may require user confirmation). "
        "Refused while a password field has focus.",
        ClipboardGetArgs, clipboard_get, "sensitive", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "clipboard_set",
        "Set the clipboard to text or a base64 PNG.",
        ClipboardSetArgs, clipboard_set, "act",
        RetryPolicy.pre_side_effect(), idempotent=True))
    registry.register(ToolSpec(
        "clipboard_paste",
        "Paste text into the focused app via clipboard + Cmd+V, then "
        "restore the previous clipboard. Preferred over keyboard_type for "
        "long text.",
        ClipboardPasteArgs, clipboard_paste, "act",
        RetryPolicy.pre_side_effect(), idempotent=False))
```

Wire it: in `src/hands/container.py` after `self.keyboard = ...`:

```python
        self.clipboard = ClipboardService(self.driver, self.keyboard,
                                          config)
```

and in `src/hands/tools/__init__.py` add `clipboard` to the imports and `clipboard.register(registry, container)` to `register_builtin_tools`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_clipboard.py -q`
Expected: 5 passed.

- [ ] **Step 8: Keyboard also refuses during secure input** (DESIGN §13.5 covers `keyboard_type` too, not just clipboard reads). Failing test first — append to `tests/unit/test_keyboard_service.py`:

```python
async def test_type_text_refuses_during_secure_input(fake_driver):
    from hands.errors import PolicyDeniedError

    cfg = HandsConfig()
    service = KeyboardService(fake_driver, cfg)
    fake_driver.set_secure_input(True)
    with pytest.raises(PolicyDeniedError):
        await service.type_text("hunter2")
    assert fake_driver.typed_text() == ""
```

Run: `uv run pytest tests/unit/test_keyboard_service.py -q` — expected: FAIL (text gets typed).

Then in `src/hands/services/keyboard.py`, at the top of `type_text` (before any chunk is posted):

```python
        if await anyio.to_thread.run_sync(
                self._driver.secure_input_active):
            raise PolicyDeniedError(
                "secure text entry is active (a password field is "
                "focused); typing is refused (DESIGN §13.5)")
```

(import `PolicyDeniedError` from `..errors`.) Re-run: expected PASS.

- [ ] **Step 9: Verify**

Run: `uv run pytest -q`
Expected: all pass (update any test asserting the total tool list: now 14).

---

### Task 3: Window driver surface (types + fake virtual windows)

**Files:**
- Modify: `src/hands/types.py` (append `WindowInfo`)
- Modify: `src/hands/driver/base.py` (protocol additions)
- Modify: `src/hands/driver/fake.py` (virtual window model)
- Test: `tests/unit/test_fake_driver.py` (append)

**Interfaces:**
- Consumes: `Region` (M1), `TargetNotFoundError` (M1), fake internals.
- Produces:
  - `WindowInfo(window_ref: str, app_name: str, bundle_id: str | None, pid: int, title: str, bounds: Region, focused: bool, minimized: bool)` frozen dataclass in `hands.types`. `window_ref` is opaque, format `"{pid}:{window_number}"`.
  - `Driver` protocol additions: `list_windows(on_screen_only: bool) -> list[WindowInfo]`; `window_perform(window_ref: str, action: Literal["move", "resize", "minimize", "unminimize", "maximize", "raise", "close"], bounds: Region | None) -> None` (raises `TargetNotFoundError` for stale refs; `move`/`resize` require `bounds`).
  - `FakeDriver` additions: `add_window(app_name, bundle_id, pid, title, bounds, focused=False) -> str` (returns the ref), window mutations recorded as `("window", ref, action)` events; minimized windows are excluded when `on_screen_only=True`; `raise` focuses the target and unfocuses all others; `close` removes the window.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_fake_driver.py`:

```python
from hands.errors import TargetNotFoundError
from hands.types import WindowInfo


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fake_driver.py -q`
Expected: FAIL — `ImportError: WindowInfo`.

- [ ] **Step 3: Implement**

Append to `src/hands/types.py`:

```python
@dataclass(frozen=True, slots=True)
class WindowInfo:
    window_ref: str            # opaque "{pid}:{window_number}" (DESIGN §4.8)
    app_name: str
    bundle_id: str | None
    pid: int
    title: str
    bounds: Region
    focused: bool
    minimized: bool
```

Add to the `Driver` protocol in `src/hands/driver/base.py`:

```python
    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]: ...
    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None: ...
```

In `src/hands/driver/fake.py` add a mutable internal record and the protocol methods:

```python
from dataclasses import dataclass as _dataclass


@_dataclass
class _FakeWindow:
    number: int
    app_name: str
    bundle_id: str | None
    pid: int
    title: str
    bounds: Region
    focused: bool = False
    minimized: bool = False

    @property
    def ref(self) -> str:
        return f"{self.pid}:{self.number}"

    def to_info(self) -> WindowInfo:
        return WindowInfo(self.ref, self.app_name, self.bundle_id,
                          self.pid, self.title, self.bounds,
                          self.focused, self.minimized)
```

To `FakeDriver.__init__` add `self._windows: list[_FakeWindow] = []` and `self._next_window_number = 1`. Methods:

```python
    def add_window(self, app_name: str, bundle_id: str | None, pid: int,
                   title: str, bounds: Region,
                   focused: bool = False) -> str:
        win = _FakeWindow(self._next_window_number, app_name, bundle_id,
                          pid, title, bounds, focused)
        self._next_window_number += 1
        if focused:
            for other in self._windows:
                other.focused = False
        self._windows.append(win)
        return win.ref

    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]:
        self._maybe_fail("list_windows")
        return [w.to_info() for w in self._windows
                if not (on_screen_only and w.minimized)]

    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None:
        self._maybe_fail("window_perform")
        win = next((w for w in self._windows if w.ref == window_ref), None)
        if win is None:
            raise TargetNotFoundError(
                f"window {window_ref} not found",
                details={"candidates": [w.ref for w in self._windows]})
        if action in ("move", "resize"):
            if bounds is None:
                raise InvalidArgsError(f"{action} requires bounds")
            win.bounds = bounds
        elif action == "minimize":
            win.minimized = True
            win.focused = False
        elif action == "unminimize":
            win.minimized = False
        elif action == "maximize":
            win.bounds = self._display.bounds_pt
        elif action == "raise":
            for other in self._windows:
                other.focused = False
            win.focused = True
            win.minimized = False
        elif action == "close":
            self._windows.remove(win)
        else:
            raise InvalidArgsError(f"unknown window action: {action}")
        self.events.append(("window", window_ref, action))
```

(import `InvalidArgsError`, `TargetNotFoundError` from `..errors` and `WindowInfo` from `..types` in `fake.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fake_driver.py -q`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 4: Window service and tools

**Files:**
- Create: `src/hands/services/windows.py`, `src/hands/tools/windows.py`
- Modify: `src/hands/registry.py` (add `ToolSpec.escalate`), `src/hands/container.py`, `src/hands/tools/__init__.py`
- Test: `tests/unit/test_window_service.py`, `tests/unit/test_tools_windows.py`

**Interfaces:**
- Consumes: Task 3 driver surface, `TargetNotFoundError` (M1).
- Produces:
  - `WindowService(driver)` with:
    - `async list(app: str | None = None, on_screen_only: bool = True) -> list[WindowInfo]` — `app` filters on bundle id or name (case-insensitive).
    - `async focus(window_ref: str | None = None, app: str | None = None, title_match: str | None = None) -> WindowInfo` — performs `raise` on the resolved window.
    - `async manage(window_ref: str, action: str, bounds: Region | None = None) -> WindowInfo` — returns the post-action `WindowInfo` (pre-action snapshot for `close`).
    - Stale-ref recovery (DESIGN §4.8/§9.2): the service snapshots every `WindowInfo` it returns; when a ref no longer resolves, it fuzzy-matches the snapshot against current windows (same pid, `difflib.SequenceMatcher` title ratio ≥ 0.7) before failing with `TargetNotFoundError` whose `details["candidates"]` lists the app's current window titles+refs.
  - `ToolSpec.escalate: Callable[[BaseModel], bool] | None = None` — new optional field; when set and it returns True for the validated args, the dispatcher treats the call as `policy_class="sensitive"` (used by `window_manage(action="close")` here and `app_close(force=true)` in Task 5). Dispatcher change is one line where the `ActionDescriptor` is built.
  - Tools: `window_list {app?, on_screen_only=true}` (read) → `{ok, windows: [...]}`; `window_focus {window_ref? | app?+title_match?}` (act, R:pre, I:effectively) → `{ok, window}`; `window_manage {window_ref, action, bounds?}` (act; escalates to sensitive for `close`) → `{ok, window}`.

- [ ] **Step 1: Write the failing service tests** — `tests/unit/test_window_service.py`:

```python
import pytest

from hands.errors import TargetNotFoundError
from hands.services.windows import WindowService
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    return WindowService(fake_driver)


def _seed(drv):
    a = drv.add_window("TextEdit", "com.apple.TextEdit", 42, "Notes.txt",
                       Region(0, 0, 800, 600), focused=True)
    b = drv.add_window("Safari", "com.apple.Safari", 50, "Apple",
                       Region(100, 100, 1200, 700))
    return a, b


async def test_list_filters_by_app(fake_driver, service):
    _seed(fake_driver)
    assert len(await service.list()) == 2
    (w,) = await service.list(app="safari")
    assert w.app_name == "Safari"
    (w2,) = await service.list(app="com.apple.TextEdit")
    assert w2.pid == 42


async def test_focus_by_app_and_title(fake_driver, service):
    _seed(fake_driver)
    win = await service.focus(app="Safari", title_match="Apple")
    assert win.focused


async def test_manage_move_returns_updated_info(fake_driver, service):
    a, _ = _seed(fake_driver)
    win = await service.manage(a, "move", Region(5, 5, 800, 600))
    assert win.bounds == Region(5, 5, 800, 600)


async def test_stale_ref_reresolves_by_fuzzy_title(fake_driver, service):
    a, _ = _seed(fake_driver)
    # Service must have seen the window once to snapshot it.
    await service.list()
    # Simulate the window being replaced: same pid, slightly new title.
    fake_driver.window_perform(a, "close", None)
    fake_driver.add_window("TextEdit", "com.apple.TextEdit", 42,
                           "Notes.txt — Edited", Region(0, 0, 800, 600))
    win = await service.focus(window_ref=a)
    assert win.title == "Notes.txt — Edited" and win.focused


async def test_unresolvable_ref_lists_candidates(fake_driver, service):
    _seed(fake_driver)
    await service.list()
    with pytest.raises(TargetNotFoundError) as ei:
        await service.focus(window_ref="42:999")
    assert ei.value.details["candidates"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_window_service.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.windows`.

- [ ] **Step 3: Implement `src/hands/services/windows.py`**

```python
"""Window management with stale-ref recovery (DESIGN §4.8, §9.2)."""
from __future__ import annotations

import difflib

import anyio

from ..driver.base import Driver
from ..errors import InvalidArgsError, TargetNotFoundError
from ..types import Region, WindowInfo

FUZZY_TITLE_RATIO = 0.7


class WindowService:
    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._snapshots: dict[str, WindowInfo] = {}

    async def list(self, app: str | None = None,
                   on_screen_only: bool = True) -> list[WindowInfo]:
        wins = await anyio.to_thread.run_sync(
            self._driver.list_windows, on_screen_only)
        if app is not None:
            needle = app.lower()
            wins = [w for w in wins
                    if needle in (w.bundle_id or "").lower()
                    or needle in w.app_name.lower()]
        for w in wins:
            self._snapshots[w.window_ref] = w
        return wins

    async def focus(self, window_ref: str | None = None,
                    app: str | None = None,
                    title_match: str | None = None) -> WindowInfo:
        win = await self._resolve(window_ref, app, title_match)
        await anyio.to_thread.run_sync(
            self._driver.window_perform, win.window_ref, "raise", None)
        return await self._refresh(win.window_ref)

    async def manage(self, window_ref: str, action: str,
                     bounds: Region | None = None) -> WindowInfo:
        win = await self._resolve(window_ref, None, None)
        await anyio.to_thread.run_sync(
            self._driver.window_perform, win.window_ref, action, bounds)
        if action == "close":
            self._snapshots.pop(win.window_ref, None)
            return win
        return await self._refresh(win.window_ref)

    async def _refresh(self, ref: str) -> WindowInfo:
        for w in await self.list(on_screen_only=False):
            if w.window_ref == ref:
                return w
        raise TargetNotFoundError(f"window {ref} vanished after action")

    async def _resolve(self, window_ref: str | None, app: str | None,
                       title_match: str | None) -> WindowInfo:
        current = await self.list(on_screen_only=False)
        if window_ref is not None:
            for w in current:
                if w.window_ref == window_ref:
                    return w
            # Stale ref: fuzzy re-resolution against the last snapshot.
            old = self._snapshots.get(window_ref)
            if old is not None:
                best, best_ratio = None, 0.0
                for w in current:
                    if w.pid != old.pid:
                        continue
                    ratio = difflib.SequenceMatcher(
                        None, old.title.lower(), w.title.lower()).ratio()
                    if ratio > best_ratio:
                        best, best_ratio = w, ratio
                if best is not None and best_ratio >= FUZZY_TITLE_RATIO:
                    return best
            raise TargetNotFoundError(
                f"window {window_ref} not found",
                details={"candidates": [
                    {"window_ref": w.window_ref, "title": w.title,
                     "app": w.app_name} for w in current]},
                remediation="call window_list and pick a current ref")
        if app is None and title_match is None:
            raise InvalidArgsError(
                "provide window_ref, or app and/or title_match")
        candidates = current
        if app is not None:
            needle = app.lower()
            candidates = [w for w in candidates
                          if needle in (w.bundle_id or "").lower()
                          or needle in w.app_name.lower()]
        if title_match is not None:
            needle = title_match.lower()
            scored = sorted(
                ((difflib.SequenceMatcher(
                    None, needle, w.title.lower()).ratio(), w)
                 for w in candidates),
                key=lambda t: -t[0])
            candidates = [w for r, w in scored
                          if needle in w.title.lower()
                          or r >= FUZZY_TITLE_RATIO]
        if not candidates:
            raise TargetNotFoundError(
                f"no window matches app={app!r} title={title_match!r}",
                details={"candidates": [
                    {"window_ref": w.window_ref, "title": w.title,
                     "app": w.app_name} for w in current]})
        return candidates[0]
```

- [ ] **Step 4: Run service tests to verify they pass**

Run: `uv run pytest tests/unit/test_window_service.py -q`
Expected: 5 passed.

- [ ] **Step 5: Add `ToolSpec.escalate` with a failing dispatcher test**

Append to `tests/unit/test_dispatcher.py` (M1 file):

```python
async def test_escalate_marks_call_sensitive(dispatcher_env):
    """ToolSpec.escalate upgrades policy_class for matching args (M3)."""
    from pydantic import BaseModel

    from hands.registry import ToolSpec
    from hands.retry import RetryPolicy

    seen: list = []

    class Recorder:
        def authorize(self, action):
            seen.append(action.policy_class)
            from hands.permissions import Allowed
            return Allowed()

    class Args(BaseModel, extra="forbid"):
        force: bool = False

    async def handler(args, ctx):
        return {}

    env = dispatcher_env
    env.registry.register(ToolSpec(
        "demo_close", "d", Args, handler, "act",
        RetryPolicy.none(), idempotent=False,
        escalate=lambda a: a.force))
    env.dispatcher._permissions = Recorder()
    await env.dispatcher.dispatch("demo_close", {"force": False})
    await env.dispatcher.dispatch("demo_close", {"force": True})
    assert seen == ["act", "sensitive"]
```

(Adapt the fixture name to M1's actual dispatcher test fixture; it builds registry + dispatcher over the fake driver. If M1's `Allowed`/`Denied` live under different names in `hands.permissions`, keep those names.)

Run: `uv run pytest tests/unit/test_dispatcher.py -q` — expected: FAIL (`unexpected keyword 'escalate'`).

Implement — in `src/hands/registry.py` add to `ToolSpec`:

```python
    escalate: Callable[[BaseModel], bool] | None = None
```

In `src/hands/dispatcher.py`, where the `ActionDescriptor` is built (phase 3), replace the policy-class source:

```python
            policy_class = spec.policy_class
            if spec.escalate is not None and spec.escalate(args):
                policy_class = "sensitive"
            action = ActionDescriptor(tool_name, policy_class)
```

Run: `uv run pytest tests/unit/test_dispatcher.py -q` — expected: PASS.

- [ ] **Step 6: Implement `src/hands/tools/windows.py`** (write `tests/unit/test_tools_windows.py` first, same pattern as Task 2's tool tests: build a `SimpleNamespace(windows=WindowService(driver))` container, register, assert `window_list` returns seeded windows, `window_focus` focuses, `window_manage` moves, and `registry.get("window_manage").escalate(args)` is True only for `action="close"`):

```python
"""MCP window tools (DESIGN §5.10–5.12)."""
from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import Region


class WindowListArgs(BaseModel, extra="forbid"):
    app: str | None = None
    on_screen_only: bool = True


class WindowFocusArgs(BaseModel, extra="forbid"):
    window_ref: str | None = None
    app: str | None = None
    title_match: str | None = None

    @model_validator(mode="after")
    def _some_target(self):
        if self.window_ref is None and self.app is None \
                and self.title_match is None:
            raise ValueError("provide window_ref, app, or title_match")
        return self


class BoundsArg(BaseModel, extra="forbid"):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class WindowManageArgs(BaseModel, extra="forbid"):
    window_ref: str
    action: Literal["move", "resize", "minimize", "unminimize",
                    "maximize", "close"]
    bounds: BoundsArg | None = None

    @model_validator(mode="after")
    def _bounds_when_needed(self):
        if self.action in ("move", "resize") and self.bounds is None:
            raise ValueError(f"{self.action} requires bounds")
        return self


def _win_dict(w) -> dict:
    d = dataclasses.asdict(w)
    return d


def register(registry: ToolRegistry, container) -> None:
    windows = container.windows

    async def window_list(args: WindowListArgs, ctx) -> dict:
        wins = await windows.list(args.app, args.on_screen_only)
        return {"windows": [_win_dict(w) for w in wins]}

    async def window_focus(args: WindowFocusArgs, ctx) -> dict:
        win = await windows.focus(args.window_ref, args.app,
                                  args.title_match)
        return {"window": _win_dict(win)}

    async def window_manage(args: WindowManageArgs, ctx) -> dict:
        bounds = (Region(**args.bounds.model_dump())
                  if args.bounds else None)
        win = await windows.manage(args.window_ref, args.action, bounds)
        return {"window": _win_dict(win)}

    registry.register(ToolSpec(
        "window_list",
        "List windows (optionally filtered by app bundle id or name). "
        "Returns window_ref handles for window_focus/window_manage.",
        WindowListArgs, window_list, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "window_focus",
        "Focus (raise) a window by window_ref, or by app and/or "
        "title_match. Stale refs are re-resolved by pid + fuzzy title.",
        WindowFocusArgs, window_focus, "act",
        RetryPolicy.pre_side_effect(), idempotent=True))
    registry.register(ToolSpec(
        "window_manage",
        "move/resize/minimize/unminimize/maximize/close a window. "
        "close may trigger 'Don't Save' dialogs and needs confirmation "
        "under the default policy.",
        WindowManageArgs, window_manage, "act",
        RetryPolicy.pre_side_effect(), idempotent=True,
        escalate=lambda a: a.action == "close"))
```

Wire it: container gains `self.windows = WindowService(self.driver)` (after `self.clipboard`); `tools/__init__.py` registers `windows`.

- [ ] **Step 7: Verify**

Run: `uv run pytest -q`
Expected: all pass (tool total now 17).

---

### Task 5: App driver surface, service, and tools

**Files:**
- Modify: `src/hands/types.py` (append `AppInfo`), `src/hands/driver/base.py`, `src/hands/driver/fake.py`
- Create: `src/hands/services/apps.py`, `src/hands/tools/apps.py`
- Modify: `src/hands/container.py`, `src/hands/tools/__init__.py`
- Test: `tests/unit/test_fake_driver.py` (append), `tests/unit/test_app_service.py`, `tests/unit/test_tools_apps.py`

**Interfaces:**
- Consumes: Tasks 3–4; `Waiter` (M2 — `AppService` uses it for `wait_for_window`); `ToolTimeoutError` (M1).
- Produces:
  - `AppInfo(bundle_id: str | None, name: str, pid: int, frontmost: bool)` frozen dataclass in `hands.types`.
  - `Driver` additions: `running_apps() -> list[AppInfo]`, `launch_app(ident: str) -> AppInfo` (raises `TargetNotFoundError` for unknown apps), `activate_app(pid: int) -> None`, `terminate_app(pid: int, force: bool) -> None`.
  - `FakeDriver` additions: `install_app(name: str, bundle_id: str)` makes an app launchable; `launch_app` assigns a fresh pid (1000, 1001, …), marks it frontmost, and opens one window titled after the app; `activate_app` sets frontmost + focuses its windows; `terminate_app` removes the app and its windows; events `("app", pid, "launch"|"activate"|"terminate"|"force_terminate")`.
  - `AppService(driver, waiter)` with:
    - `async open(app: str, wait_for_window: bool = True, timeout_ms: int = 15000) -> tuple[AppInfo, WindowInfo | None]` — activates if already running, else launches; waits on `{"type": "window_present", "app": app}` when asked and raises `ToolTimeoutError` if it never appears.
    - `async close(app: str, force: bool = False) -> None`
    - `async list_running() -> list[AppInfo]`
  - Tools: `app_open {app, wait_for_window=true, timeout_ms=15000}` (act, R:pre, I:effectively) → `{ok, app, window?}`; `app_close {app, force=false}` (act; **escalates to sensitive when force**) → `{ok}`; `app_list {}` (read) → `{ok, apps, frontmost}`.
  - Note: `open` uses waiter condition `window_present`, which Task 6 adds. **Do Task 6's waiter checker first if executing out of order**; in-order execution is fine because this task's tests monkeypatch nothing — write `AppService` against the waiter interface and let its test seed the window synchronously before `open` returns (the fake launches a window immediately, so the wait meets on the first poll — but only after Task 6 lands). To keep tasks independent: **this task's service tests pass `wait_for_window=False`**; the wait path is covered in Task 6's tests.

- [ ] **Step 1: Write the failing driver tests** — append to `tests/unit/test_fake_driver.py`:

```python
from hands.types import AppInfo


def test_install_launch_activate_terminate():
    drv = FakeDriver()
    drv.install_app("Notes", "com.apple.Notes")
    drv.install_app("Safari", "com.apple.Safari")
    notes = drv.launch_app("com.apple.Notes")
    assert isinstance(notes, AppInfo) and notes.frontmost
    safari = drv.launch_app("Safari")            # by name too
    assert safari.frontmost
    apps = {a.name: a for a in drv.running_apps()}
    assert not apps["Notes"].frontmost
    drv.activate_app(notes.pid)
    apps = {a.name: a for a in drv.running_apps()}
    assert apps["Notes"].frontmost
    # Launching opened one window per app.
    assert len(drv.list_windows(False)) == 2
    drv.terminate_app(safari.pid, force=False)
    assert len(drv.running_apps()) == 1
    assert len(drv.list_windows(False)) == 1


def test_launch_unknown_app():
    drv = FakeDriver()
    with pytest.raises(TargetNotFoundError):
        drv.launch_app("com.example.Ghost")


def test_activating_running_app_is_effectively_idempotent():
    drv = FakeDriver()
    drv.install_app("Notes", "com.apple.Notes")
    a = drv.launch_app("Notes")
    again = drv.launch_app("Notes")
    assert again.pid == a.pid          # no second instance
```

- [ ] **Step 2: Run driver tests to verify they fail**

Run: `uv run pytest tests/unit/test_fake_driver.py -q`
Expected: FAIL — `ImportError: AppInfo`.

- [ ] **Step 3: Implement the driver surface**

Append to `src/hands/types.py`:

```python
@dataclass(frozen=True, slots=True)
class AppInfo:
    bundle_id: str | None
    name: str
    pid: int
    frontmost: bool
```

Add to the `Driver` protocol:

```python
    def running_apps(self) -> list[AppInfo]: ...
    def launch_app(self, ident: str) -> AppInfo: ...
    def activate_app(self, pid: int) -> None: ...
    def terminate_app(self, pid: int, force: bool) -> None: ...
```

In `FakeDriver.__init__` add:

```python
        self._installed: dict[str, str] = {}      # bundle_id -> name
        self._running: dict[int, dict] = {}       # pid -> {name, bundle_id, frontmost}
        self._next_pid = 1000
```

and methods:

```python
    def install_app(self, name: str, bundle_id: str) -> None:
        self._installed[bundle_id] = name

    def _find_installed(self, ident: str) -> tuple[str, str] | None:
        for bid, name in self._installed.items():
            if ident in (bid, name):
                return bid, name
        return None

    def running_apps(self) -> list[AppInfo]:
        self._maybe_fail("running_apps")
        return [AppInfo(r["bundle_id"], r["name"], pid, r["frontmost"])
                for pid, r in self._running.items()]

    def launch_app(self, ident: str) -> AppInfo:
        self._maybe_fail("launch_app")
        for pid, r in self._running.items():
            if ident in (r["bundle_id"], r["name"]):
                self.activate_app(pid)
                return AppInfo(r["bundle_id"], r["name"], pid, True)
        found = self._find_installed(ident)
        if found is None:
            raise TargetNotFoundError(
                f"no such app: {ident}",
                details={"installed": sorted(self._installed)})
        bid, name = found
        pid = self._next_pid
        self._next_pid += 1
        for r in self._running.values():
            r["frontmost"] = False
        self._running[pid] = {"name": name, "bundle_id": bid,
                              "frontmost": True}
        self.add_window(name, bid, pid, name,
                        Region(50, 50, 1000, 700), focused=True)
        self.events.append(("app", pid, "launch"))
        return AppInfo(bid, name, pid, True)

    def activate_app(self, pid: int) -> None:
        self._maybe_fail("activate_app")
        if pid not in self._running:
            raise TargetNotFoundError(f"pid {pid} not running")
        for p, r in self._running.items():
            r["frontmost"] = (p == pid)
        for w in self._windows:
            w.focused = (w.pid == pid)
        self.events.append(("app", pid, "activate"))

    def terminate_app(self, pid: int, force: bool) -> None:
        self._maybe_fail("terminate_app")
        if pid not in self._running:
            raise TargetNotFoundError(f"pid {pid} not running")
        del self._running[pid]
        self._windows = [w for w in self._windows if w.pid != pid]
        self.events.append(
            ("app", pid, "force_terminate" if force else "terminate"))
```

Run: `uv run pytest tests/unit/test_fake_driver.py -q` — expected: PASS.

- [ ] **Step 4: Write the failing service/tool tests** — `tests/unit/test_app_service.py`:

```python
import pytest

from hands.errors import TargetNotFoundError
from hands.services.apps import AppService

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    # waiter=None is fine while wait_for_window=False (Task 6 wires it).
    return AppService(fake_driver, waiter=None)


async def test_open_launches_then_activates(fake_driver, service):
    fake_driver.install_app("Notes", "com.apple.Notes")
    app, _ = await service.open("Notes", wait_for_window=False)
    assert app.frontmost
    again, _ = await service.open("Notes", wait_for_window=False)
    assert again.pid == app.pid


async def test_open_unknown_app(fake_driver, service):
    with pytest.raises(TargetNotFoundError):
        await service.open("Ghost", wait_for_window=False)


async def test_close(fake_driver, service):
    fake_driver.install_app("Notes", "com.apple.Notes")
    await service.open("Notes", wait_for_window=False)
    await service.close("Notes")
    assert await service.list_running() == []


async def test_close_not_running(fake_driver, service):
    with pytest.raises(TargetNotFoundError):
        await service.close("Notes")
```

`tests/unit/test_tools_apps.py` (same `SimpleNamespace` pattern; container needs `apps=AppService(driver, waiter=None)`):

```python
async def test_app_open_and_list(env):
    env.driver.install_app("Notes", "com.apple.Notes")
    res = await _call(env, "app_open",
                      {"app": "Notes", "wait_for_window": False})
    assert res["app"]["name"] == "Notes"
    listing = await _call(env, "app_list", {})
    assert listing["frontmost"]["name"] == "Notes"


async def test_app_close_force_escalates(env):
    spec = env.registry.get("app_close")
    assert spec.escalate(spec.args_model.model_validate(
        {"app": "Notes", "force": True})) is True
    assert spec.escalate(spec.args_model.model_validate(
        {"app": "Notes"})) is False
```

Run: `uv run pytest tests/unit/test_app_service.py tests/unit/test_tools_apps.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.apps`.

- [ ] **Step 5: Implement `src/hands/services/apps.py`**

```python
"""Application lifecycle (DESIGN §4.9)."""
from __future__ import annotations

import anyio

from ..driver.base import Driver
from ..errors import TargetNotFoundError, ToolTimeoutError
from ..types import AppInfo, WindowInfo


class AppService:
    def __init__(self, driver: Driver, waiter) -> None:
        self._driver = driver
        self._waiter = waiter

    async def list_running(self) -> list[AppInfo]:
        return await anyio.to_thread.run_sync(self._driver.running_apps)

    async def _find_running(self, ident: str) -> AppInfo | None:
        needle = ident.lower()
        for a in await self.list_running():
            if needle in ((a.bundle_id or "").lower(), a.name.lower()):
                return a
        return None

    async def open(self, app: str, wait_for_window: bool = True,
                   timeout_ms: int = 15000
                   ) -> tuple[AppInfo, WindowInfo | None]:
        running = await self._find_running(app)
        if running is not None:
            await anyio.to_thread.run_sync(
                self._driver.activate_app, running.pid)
            info = await self._find_running(app)
        else:
            info = await anyio.to_thread.run_sync(
                self._driver.launch_app, app)
        window: WindowInfo | None = None
        if wait_for_window:
            res = await self._waiter.wait_for(
                {"type": "window_present", "app": app}, timeout_ms)
            if not res.met:
                raise ToolTimeoutError(
                    f"{app} produced no window within {timeout_ms} ms",
                    details={"app": app})
            wins = [w for w in await anyio.to_thread.run_sync(
                        self._driver.list_windows, True)
                    if w.pid == info.pid]
            window = wins[0] if wins else None
        return info, window

    async def close(self, app: str, force: bool = False) -> None:
        running = await self._find_running(app)
        if running is None:
            raise TargetNotFoundError(f"{app} is not running")
        await anyio.to_thread.run_sync(
            self._driver.terminate_app, running.pid, force)
```

- [ ] **Step 6: Implement `src/hands/tools/apps.py`**

```python
"""MCP app tools (DESIGN §5.13)."""
from __future__ import annotations

import dataclasses

from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy


class AppOpenArgs(BaseModel, extra="forbid"):
    app: str = Field(min_length=1)
    wait_for_window: bool = True
    timeout_ms: int = Field(default=15_000, ge=0, le=120_000)


class AppCloseArgs(BaseModel, extra="forbid"):
    app: str = Field(min_length=1)
    force: bool = False


class AppListArgs(BaseModel, extra="forbid"):
    pass


def register(registry: ToolRegistry, container) -> None:
    apps = container.apps

    async def app_open(args: AppOpenArgs, ctx) -> dict:
        info, window = await apps.open(args.app, args.wait_for_window,
                                       args.timeout_ms)
        out = {"app": dataclasses.asdict(info)}
        if window is not None:
            out["window"] = dataclasses.asdict(window)
        return out

    async def app_close(args: AppCloseArgs, ctx) -> dict:
        await apps.close(args.app, args.force)
        return {}

    async def app_list(args: AppListArgs, ctx) -> dict:
        running = await apps.list_running()
        frontmost = next((dataclasses.asdict(a) for a in running
                          if a.frontmost), None)
        return {"apps": [dataclasses.asdict(a) for a in running],
                "frontmost": frontmost}

    registry.register(ToolSpec(
        "app_open",
        "Launch an app by bundle id (preferred) or name; activates it if "
        "already running. wait_for_window waits for its first window.",
        AppOpenArgs, app_open, "act", RetryPolicy.pre_side_effect(),
        idempotent=True))
    registry.register(ToolSpec(
        "app_close",
        "Quit an app gracefully; force=true force-terminates (sensitive, "
        "needs confirmation under the default policy).",
        AppCloseArgs, app_close, "act", RetryPolicy.pre_side_effect(),
        idempotent=True, escalate=lambda a: a.force))
    registry.register(ToolSpec(
        "app_list",
        "List running apps and the frontmost one.",
        AppListArgs, app_list, "read", RetryPolicy.read(),
        idempotent=True))
```

Wire the container (order matters — `apps` needs `waiter`):

```python
        self.windows = WindowService(self.driver)
        self.apps = AppService(self.driver, self.waiter)
```

and register `apps` in `tools/__init__.py`.

- [ ] **Step 7: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_app_service.py tests/unit/test_tools_apps.py -q` then `uv run pytest -q`
Expected: all pass (tool total now 20).

---

### Task 6: Waiter and verification extensions (windows/apps/clipboard)

**Files:**
- Modify: `src/hands/services/waiter.py`, `src/hands/services/verification.py`, `src/hands/container.py`
- Test: `tests/unit/test_waiter.py` (append), `tests/unit/test_verification.py` (append)

**Interfaces:**
- Consumes: Tasks 3–5 driver surfaces, `ClipboardService` (Task 2).
- Produces:
  - `Waiter(screenshots, ocr, config, driver=None)` — new optional `driver` kwarg (M2 call sites unchanged). New checkers: `window_present {app? , title?}`, `window_gone {window_ref? , title?}`, `app_frontmost {app}` — all read the driver directly (cheap snapshots, no service dependency, no circular wiring).
  - `VerificationEngine(screenshots, ocr, driver, config, clipboard=None)` — new strategies `window_present`, `window_gone`, `clipboard_contains {text}`; `_KNOWN_TYPES` extended. Evidence for clipboard never includes content — only `{"matched": bool, "clipboard_len": int}` (redaction invariant).
  - Container: `self.waiter = Waiter(self.screenshots, self.ocr, config, driver=self.driver)` and `self.verification = VerificationEngine(self.screenshots, self.ocr, self.driver, config, clipboard=self.clipboard)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_waiter.py` (the fixture gains `driver=fake_driver` in the `Waiter(...)` call):

```python
async def test_window_present_and_gone(fake_driver, waiter):
    from hands.types import Region
    res = await waiter.wait_for(
        {"type": "window_present", "app": "Notes"}, 40)
    assert res.met is False
    fake_driver.add_window("Notes", "com.apple.Notes", 7, "My Note",
                           Region(0, 0, 400, 300))
    res = await waiter.wait_for(
        {"type": "window_present", "app": "Notes", "title": "note"}, 500)
    assert res.met
    res = await waiter.wait_for(
        {"type": "window_gone", "title": "My Note"}, 40)
    assert res.met is False


async def test_app_frontmost(fake_driver, waiter):
    fake_driver.install_app("Notes", "com.apple.Notes")
    fake_driver.launch_app("Notes")
    res = await waiter.wait_for(
        {"type": "app_frontmost", "app": "com.apple.Notes"}, 500)
    assert res.met
```

Append to `tests/unit/test_verification.py` (the `env` fixture builds the engine with `clipboard=ClipboardService(fake_driver, KeyboardService(fake_driver, cfg), cfg)`):

```python
async def test_window_present_strategy(env):
    driver, _, engine = env
    from hands.types import Region
    driver.add_window("Notes", "com.apple.Notes", 7, "My Note",
                      Region(0, 0, 400, 300))
    res = await engine.verify(Expectation.from_wire(
        {"type": "window_present", "title": "My Note"}))
    assert res.passed and res.confidence == 1.0
    gone = await engine.verify(Expectation.from_wire(
        {"type": "window_gone", "title": "My Note"}))
    assert not gone.passed


async def test_clipboard_contains_redacts_evidence(env):
    driver, _, engine = env
    from hands.types import ClipboardContent
    driver.clipboard_write(ClipboardContent("text", text="secret token"))
    res = await engine.verify(Expectation.from_wire(
        {"type": "clipboard_contains", "text": "token"}))
    assert res.passed
    assert "secret" not in str(res.evidence)
    assert res.evidence["clipboard_len"] == len("secret token")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_waiter.py tests/unit/test_verification.py -q`
Expected: FAIL — `InvalidArgsError: unknown condition type` / unknown expectation type.

- [ ] **Step 3: Implement**

In `src/hands/services/waiter.py`: `__init__` gains `driver=None`, stores `self._driver = driver`, and the checker table gains three entries; add the methods:

```python
    async def _window_present(self, cond: dict, scratch: dict):
        wins = await anyio.to_thread.run_sync(
            self._driver.list_windows, True)
        app = str(cond.get("app", "")).lower()
        title = str(cond.get("title", "")).lower()
        matches = [w for w in wins
                   if (not app or app in (w.bundle_id or "").lower()
                       or app in w.app_name.lower())
                   and (not title or title in w.title.lower())]
        evidence = {"windows": [
            {"window_ref": w.window_ref, "title": w.title,
             "app": w.app_name} for w in matches]}
        return bool(matches), evidence

    async def _window_gone(self, cond: dict, scratch: dict):
        met, evidence = await self._window_present(cond, scratch)
        return not met, evidence

    async def _app_frontmost(self, cond: dict, scratch: dict):
        apps = await anyio.to_thread.run_sync(self._driver.running_apps)
        needle = str(cond.get("app", "")).lower()
        front = next((a for a in apps if a.frontmost), None)
        met = front is not None and needle in (
            (front.bundle_id or "").lower(), front.name.lower())
        return met, {"frontmost": front.name if front else None}
```

In `src/hands/services/verification.py`: `__init__` gains `clipboard=None`; `_KNOWN_TYPES` gains `"window_present", "window_gone", "clipboard_contains"`; add:

```python
    async def _window_present(self, params, shot, baseline):
        import anyio as _anyio
        wins = await _anyio.to_thread.run_sync(
            self._driver.list_windows, True)
        title = str(params.get("title", "")).lower()
        app = str(params.get("app", "")).lower()
        matches = [w for w in wins
                   if (not title or title in w.title.lower())
                   and (not app or app in (w.bundle_id or "").lower()
                        or app in w.app_name.lower())]
        return VerificationResult(
            bool(matches), 1.0 if matches else 0.0,
            {"windows": [{"window_ref": w.window_ref, "title": w.title}
                         for w in matches]})

    async def _window_gone(self, params, shot, baseline):
        inner = await self._window_present(params, shot, baseline)
        return VerificationResult(not inner.passed,
                                  1.0 - inner.confidence, inner.evidence)

    async def _clipboard_contains(self, params, shot, baseline):
        content = await self._clipboard.get("text")
        text = content.text or ""
        needle = str(params.get("text", ""))
        matched = needle in text
        # Redaction invariant: never surface clipboard content.
        return VerificationResult(
            matched, 1.0 if matched else 0.0,
            {"matched": matched, "clipboard_len": len(text)})
```

Update `src/hands/container.py` to pass `driver=self.driver` to `Waiter` and `clipboard=self.clipboard` to `VerificationEngine`. Note the container build order must now be: state → coords → screenshots → ocr → mouse → keyboard → clipboard → windows → **waiter** → apps → verification.

- [ ] **Step 4: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_waiter.py tests/unit/test_verification.py -q` then `uv run pytest -q`
Expected: all pass. Also confirm `AppService.open(wait_for_window=True)` now works end to end: append to `tests/unit/test_app_service.py`:

```python
async def test_open_waits_for_window(fake_driver):
    from hands.config import HandsConfig
    from hands.services.coords import CoordinateMapper
    from hands.services.ocr import OCRService
    from hands.services.screenshot import ScreenshotService
    from hands.services.waiter import Waiter
    from hands.state import StateManager

    cfg = HandsConfig()
    cfg.waiter.poll_start_ms = 5
    shots = ScreenshotService(fake_driver, StateManager(cfg), cfg)
    ocr = OCRService(fake_driver,
                     CoordinateMapper(fake_driver.displays()), cfg)
    waiter = Waiter(shots, ocr, cfg, driver=fake_driver)
    service = AppService(fake_driver, waiter)
    fake_driver.install_app("Notes", "com.apple.Notes")
    app, window = await service.open("Notes")
    assert window is not None and window.pid == app.pid
```

---

### Task 7: AX tree and `get_ui_tree`

**Files:**
- Modify: `src/hands/driver/base.py` (append `AXNode`, protocol method), `src/hands/driver/fake.py`, `src/hands/config.py` (add `ax` section)
- Create: `src/hands/tools/ax.py`
- Modify: `src/hands/tools/__init__.py`
- Test: `tests/unit/test_fake_driver.py` (append), `tests/unit/test_tools_ax.py`

**Interfaces:**
- Consumes: window/app surfaces (Tasks 3, 5), `PermissionMissingError` (M1).
- Produces:
  - `AXNode(role: str, title: str | None, value: str | None, region: Region | None, actions: tuple[str, ...] = (), children: tuple["AXNode", ...] = ())` frozen dataclass in `hands.driver.base`.
  - `Driver.ax_tree(pid: int | None, max_depth: int) -> AXNode` — `pid=None` means the frontmost app; raises `PermissionMissingError` when Accessibility is not granted, `TargetNotFoundError` when the pid is gone.
  - `FakeDriver`: builds a synthetic tree from its virtual state (`AXApplication` root → `AXWindow` children with title/region), or returns a scripted tree set via `set_ax_tree(node: AXNode)`.
  - Config: `HandsConfig.ax: AXConfig(max_nodes: int = 500)`.
  - Tool `get_ui_tree {app?: str, max_depth: int = 8 (1..20)}` (read) → `{ok, tree, truncated: bool}` where `tree` is the recursive dict form of `AXNode` pruned to `config.ax.max_nodes` nodes breadth-first.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_fake_driver.py`:

```python
from hands.driver.base import AXNode


def test_fake_ax_tree_reflects_windows():
    drv = FakeDriver()
    drv.install_app("Notes", "com.apple.Notes")
    app = drv.launch_app("Notes")
    tree = drv.ax_tree(app.pid, max_depth=8)
    assert tree.role == "AXApplication"
    assert tree.children[0].role == "AXWindow"
    assert tree.children[0].title == "Notes"


def test_fake_ax_tree_scripted_override():
    drv = FakeDriver()
    node = AXNode("AXApplication", "Fixture", None, None, (), (
        AXNode("AXButton", "OK", None, Region(10, 10, 80, 30),
               ("AXPress",)),))
    drv.set_ax_tree(node)
    assert drv.ax_tree(None, 8) is node
```

`tests/unit/test_tools_ax.py` (SimpleNamespace container with `driver`, `apps`, `config`):

```python
import pytest
from types import SimpleNamespace

from hands.config import HandsConfig
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.apps import AppService
from hands.tools import ax as ax_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    driver = FakeDriver()
    container = SimpleNamespace(config=cfg, driver=driver,
                                apps=AppService(driver, waiter=None))
    reg = ToolRegistry()
    ax_tools.register(reg, container)
    return SimpleNamespace(driver=driver, registry=reg)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_get_ui_tree_serializes(env):
    env.driver.install_app("Notes", "com.apple.Notes")
    env.driver.launch_app("Notes")
    res = await _call(env, "get_ui_tree", {"app": "Notes"})
    assert res["tree"]["role"] == "AXApplication"
    assert res["truncated"] is False


async def test_get_ui_tree_node_cap(env):
    from hands.driver.base import AXNode
    kids = tuple(AXNode("AXButton", f"b{i}", None, None)
                 for i in range(600))
    env.driver.set_ax_tree(AXNode("AXApplication", "Big", None, None,
                                  (), kids))
    env.driver.install_app("Big", "com.example.Big")
    env.driver.launch_app("Big")
    res = await _call(env, "get_ui_tree", {"app": "Big"})
    assert res["truncated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_tools_ax.py -q`
Expected: FAIL — `ImportError: AXNode`.

- [ ] **Step 3: Implement**

Append to `src/hands/driver/base.py`:

```python
@dataclass(frozen=True, slots=True)
class AXNode:
    """One accessibility element (DESIGN §5.15). region is canonical
    points; None when the element has no frame."""
    role: str
    title: str | None
    value: str | None
    region: Region | None
    actions: tuple[str, ...] = ()
    children: tuple["AXNode", ...] = ()
```

and to the `Driver` protocol:

```python
    def ax_tree(self, pid: int | None, max_depth: int) -> AXNode: ...
```

`FakeDriver` — add `self._ax_override: AXNode | None = None` to `__init__` and:

```python
    def set_ax_tree(self, node: AXNode) -> None:
        self._ax_override = node

    def ax_tree(self, pid: int | None, max_depth: int) -> AXNode:
        self._maybe_fail("ax_tree")
        if self._ax_override is not None:
            return self._ax_override
        if pid is None:
            front = next((p for p, r in self._running.items()
                          if r["frontmost"]), None)
            if front is None:
                raise TargetNotFoundError("no frontmost app")
            pid = front
        if pid not in self._running:
            raise TargetNotFoundError(f"pid {pid} not running")
        windows = tuple(
            AXNode("AXWindow", w.title, None, w.bounds, ("AXRaise",))
            for w in self._windows if w.pid == pid)
        return AXNode("AXApplication", self._running[pid]["name"],
                      None, None, (), windows)
```

Config:

```python
class AXConfig(BaseModel):
    max_nodes: int = 500
```

with `ax: AXConfig = AXConfig()` on `HandsConfig`.

`src/hands/tools/ax.py`:

```python
"""get_ui_tree (DESIGN §5.15). AX ground truth alongside OCR."""
from __future__ import annotations

import anyio
from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy


class GetUiTreeArgs(BaseModel, extra="forbid"):
    app: str | None = None
    max_depth: int = Field(default=8, ge=1, le=20)


def register(registry: ToolRegistry, container) -> None:
    driver = container.driver
    apps = container.apps
    max_nodes = container.config.ax.max_nodes

    def _serialize(node, budget: list[int]) -> dict | None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        children = []
        for c in node.children:
            s = _serialize(c, budget)
            if s is None:
                break
            children.append(s)
        out: dict = {"role": node.role, "title": node.title,
                     "value": node.value, "actions": list(node.actions),
                     "children": children}
        if node.region is not None:
            out["region"] = {"x": node.region.x, "y": node.region.y,
                             "width": node.region.width,
                             "height": node.region.height}
        return out

    async def get_ui_tree(args: GetUiTreeArgs, ctx) -> dict:
        pid = None
        if args.app is not None:
            needle = args.app.lower()
            for a in await apps.list_running():
                if needle in ((a.bundle_id or "").lower(),
                              a.name.lower()):
                    pid = a.pid
                    break
        tree = await anyio.to_thread.run_sync(
            driver.ax_tree, pid, args.max_depth)
        budget = [max_nodes]
        serialized = _serialize(tree, budget)
        return {"tree": serialized, "truncated": budget[0] <= 0}

    registry.register(ToolSpec(
        "get_ui_tree",
        "Accessibility tree for an app (frontmost if omitted): roles, "
        "titles, values, clickable regions in points. Ground truth where "
        "apps expose it; use find_text where they don't.",
        GetUiTreeArgs, get_ui_tree, "read", RetryPolicy.read(),
        idempotent=True))
```

Register `ax` in `tools/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_tools_ax.py -q` then `uv run pytest -q`
Expected: all pass (tool total now 21).

---

### Task 8: PermissionEngine, confirmation hooks, rate limiting

**Files:**
- Modify: `src/hands/permissions.py` (replace stub file; keep `AllowAllPermissions` for tests), `src/hands/config.py`, `src/hands/dispatcher.py`, `src/hands/container.py`
- Test: `tests/unit/test_permissions.py`, `tests/unit/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: M1 `ActionDescriptor`/`Allowed`/`Denied` names, dispatcher pipeline, `ToolSpec.escalate` (Task 4).
- Produces (in `hands.permissions`):
  - `ActionDescriptor(tool: str, policy_class: str, target_app: str | None = None, text: str | None = None)` — extended with defaulted fields (M1 call sites keep working).
  - `NeedsConfirmation(prompt: str)` with no-op `raise_if_denied()`, joining `Allowed`/`Denied`.
  - `Rule(match_tools: tuple[str, ...] = ("*",), match_apps: tuple[str, ...] = ("*",), match_text: str | None = None, effect: Literal["allow", "deny", "confirm"] = "allow")` — glob on tools/apps (`fnmatch`), regex on text.
  - `Profile(name: str, rules: tuple[Rule, ...] = (), confirm_acts: bool = False, allow_sensitive: bool = False)`; `load_profile(config) -> Profile` mapping `strict` (confirm_acts=True), `default`, `trusted` (allow_sensitive=True).
  - `ConfirmationHook = Callable[[str, ActionDescriptor], Awaitable[bool]]`; `auto_deny_hook`, `osascript_hook` (macOS dialog via `osascript` subprocess in a thread); `make_confirm_hook(config)` picks by `config.security.confirmation` and platform.
  - `PermissionEngine(profile, confirm_hook, config)` with `authorize(action) -> Allowed | Denied | NeedsConfirmation` and `async confirm(prompt, action) -> bool`. Evaluation order: (1) deny-listed app for non-read actions → `Denied`; (2) first matching profile rule → its effect; (3) secret pattern on `action.text` → `NeedsConfirmation`; (4) class default: read/act → `Allowed` (act → `NeedsConfirmation` when `confirm_acts`), sensitive → `NeedsConfirmation` unless `allow_sensitive`.
  - Config `SecurityConfig` additions: `profile: Literal["strict", "default", "trusted"] = "default"`, `deny_apps: list[str] = ["com.apple.systempreferences*", "com.apple.Passwords*", "com.apple.keychainaccess", "com.agilebits.onepassword*", "com.1password.*"]`, `secret_patterns: list[str] = []`, `max_actions_per_s: float = 10.0`, `confirmation: Literal["dialog", "deny"] = "dialog"`.
  - Dispatcher changes: (a) build the descriptor with `target_app` from an optional `frontmost_provider: Callable[[], str | None] | None = None` constructor kwarg and `text=getattr(args, "text", None)`; (b) handle `NeedsConfirmation` by awaiting `permissions.confirm(...)` (declined → `PolicyDeniedError("user declined: ...")`); (c) rate-limit non-read tools with a 1-second sliding window of `config.security.max_actions_per_s` timestamps → `PolicyDeniedError` with `remediation="rate limit exceeded; slow down and retry"`.
  - Container: `self.permissions = PermissionEngine(load_profile(config), make_confirm_hook(config), config)`; dispatcher gets `frontmost_provider=lambda: next((a.bundle_id or a.name for a in self.driver.running_apps() if a.frontmost), None)`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_permissions.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.permissions import (
    ActionDescriptor,
    Allowed,
    Denied,
    NeedsConfirmation,
    PermissionEngine,
    Profile,
    Rule,
    load_profile,
)

pytestmark = pytest.mark.anyio


async def _yes(prompt, action):
    return True


def _engine(profile=None, cfg=None, hook=_yes):
    cfg = cfg or HandsConfig()
    return PermissionEngine(profile or load_profile(cfg), hook, cfg)


def test_read_allowed_by_default():
    d = _engine().authorize(ActionDescriptor("screenshot", "read"))
    assert isinstance(d, Allowed)


def test_act_allowed_default_confirmed_under_strict():
    cfg = HandsConfig()
    assert isinstance(
        _engine().authorize(ActionDescriptor("mouse_click", "act")),
        Allowed)
    cfg.security.profile = "strict"
    assert isinstance(
        _engine(load_profile(cfg), cfg).authorize(
            ActionDescriptor("mouse_click", "act")),
        NeedsConfirmation)


def test_sensitive_confirms_by_default_allowed_when_trusted():
    assert isinstance(
        _engine().authorize(ActionDescriptor("clipboard_get",
                                             "sensitive")),
        NeedsConfirmation)
    cfg = HandsConfig()
    cfg.security.profile = "trusted"
    assert isinstance(
        _engine(load_profile(cfg), cfg).authorize(
            ActionDescriptor("clipboard_get", "sensitive")),
        Allowed)


def test_deny_listed_app_blocks_acting_tools():
    d = _engine().authorize(ActionDescriptor(
        "mouse_click", "act", target_app="com.apple.Passwords"))
    assert isinstance(d, Denied)
    # reads are not blocked by the app deny list
    assert isinstance(_engine().authorize(ActionDescriptor(
        "screenshot", "read", target_app="com.apple.Passwords")),
        Allowed)


def test_first_matching_rule_wins():
    profile = Profile("custom", rules=(
        Rule(match_tools=("keyboard_*",), effect="deny"),
        Rule(match_tools=("*",), effect="allow"),
    ))
    engine = _engine(profile)
    assert isinstance(engine.authorize(
        ActionDescriptor("keyboard_type", "act")), Denied)
    assert isinstance(engine.authorize(
        ActionDescriptor("mouse_click", "act")), Allowed)


def test_secret_pattern_forces_confirmation():
    cfg = HandsConfig()
    cfg.security.secret_patterns = [r"(?i)password"]
    engine = _engine(load_profile(cfg), cfg)
    d = engine.authorize(ActionDescriptor(
        "keyboard_type", "act", text="my Password123"))
    assert isinstance(d, NeedsConfirmation)


async def test_confirm_delegates_to_hook():
    calls = []

    async def hook(prompt, action):
        calls.append(prompt)
        return False

    engine = _engine(hook=hook)
    ok = await engine.confirm("Allow?", ActionDescriptor("x", "sensitive"))
    assert ok is False and calls == ["Allow?"]
```

Append to `tests/unit/test_dispatcher.py`:

```python
async def test_needs_confirmation_declined_is_policy_denied(dispatcher_env):
    from hands.permissions import NeedsConfirmation

    class Confirming:
        def authorize(self, action):
            return NeedsConfirmation("really?")

        async def confirm(self, prompt, action):
            return False

    env = dispatcher_env
    env.dispatcher._permissions = Confirming()
    res = await env.dispatcher.dispatch("mouse_move", {"x": 1, "y": 1})
    assert res["ok"] is False
    assert res["error"]["code"] == "POLICY_DENIED"


async def test_rate_limit_denies_burst(dispatcher_env):
    env = dispatcher_env
    env.dispatcher._config.security.max_actions_per_s = 3
    outcomes = []
    for _ in range(5):
        res = await env.dispatcher.dispatch("mouse_move",
                                            {"x": 1, "y": 1})
        outcomes.append(res["ok"])
    assert outcomes.count(False) >= 2
    denied = [r for r in [await env.dispatcher.dispatch(
        "mouse_move", {"x": 1, "y": 1})] if not r["ok"]]
    if denied:
        assert denied[0]["error"]["code"] == "POLICY_DENIED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_permissions.py tests/unit/test_dispatcher.py -q`
Expected: FAIL — imports (`Rule`, `Profile`, `NeedsConfirmation`, `load_profile`) missing.

- [ ] **Step 3: Implement `src/hands/permissions.py`** (replacing the M1 stub file; keep the existing `Allowed`, `Denied`, and `AllowAllPermissions` — tests use them):

```python
"""Policy layer (DESIGN §7.10, §13.3). OS/TCC permissions are a different
layer (PermissionMissingError); this module only decides what the AGENT
may do (PolicyDeniedError)."""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal

import anyio

from .errors import PolicyDeniedError


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    tool: str
    policy_class: str
    target_app: str | None = None      # frontmost bundle id at call time
    text: str | None = None            # typed/pasted text, if any


@dataclass(frozen=True)
class Allowed:
    def raise_if_denied(self) -> None:
        pass


@dataclass(frozen=True)
class Denied:
    reason: str

    def raise_if_denied(self) -> None:
        raise PolicyDeniedError(self.reason)


@dataclass(frozen=True)
class NeedsConfirmation:
    prompt: str

    def raise_if_denied(self) -> None:
        pass


@dataclass(frozen=True)
class Rule:
    match_tools: tuple[str, ...] = ("*",)
    match_apps: tuple[str, ...] = ("*",)
    match_text: str | None = None
    effect: Literal["allow", "deny", "confirm"] = "allow"

    def matches(self, action: ActionDescriptor) -> bool:
        if not any(fnmatch(action.tool, g) for g in self.match_tools):
            return False
        app = action.target_app or ""
        if not any(fnmatch(app, g) for g in self.match_apps):
            return False
        if self.match_text is not None:
            if action.text is None:
                return False
            if not re.search(self.match_text, action.text):
                return False
        return True


@dataclass(frozen=True)
class Profile:
    """First matching rule wins; then per-class defaults (DESIGN §13.3)."""
    name: str
    rules: tuple[Rule, ...] = ()
    confirm_acts: bool = False
    allow_sensitive: bool = False


def load_profile(config) -> Profile:
    name = config.security.profile
    if name == "strict":
        return Profile("strict", confirm_acts=True)
    if name == "trusted":
        return Profile("trusted", allow_sensitive=True)
    return Profile("default")


ConfirmationHook = Callable[[str, ActionDescriptor], Awaitable[bool]]


async def auto_deny_hook(prompt: str, action: ActionDescriptor) -> bool:
    return False


async def osascript_hook(prompt: str, action: ActionDescriptor) -> bool:
    """macOS confirmation dialog. Runs off the event loop."""
    def _ask() -> bool:
        script = (
            'display dialog "{}" with title "Hands" '
            'buttons {{"Deny", "Allow"}} default button "Deny"'
        ).format(prompt.replace('"', "'"))
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=60)
        return proc.returncode == 0 and "Allow" in proc.stdout
    return await anyio.to_thread.run_sync(_ask)


def make_confirm_hook(config) -> ConfirmationHook:
    if config.security.confirmation == "dialog" \
            and sys.platform == "darwin":
        return osascript_hook
    return auto_deny_hook


class PermissionEngine:
    def __init__(self, profile: Profile, confirm_hook: ConfirmationHook,
                 config) -> None:
        self._profile = profile
        self._hook = confirm_hook
        self._sec = config.security

    def authorize(self, action: ActionDescriptor):
        # 1. deny-listed apps block anything that acts on them
        if action.policy_class != "read" and action.target_app:
            for glob in self._sec.deny_apps:
                if fnmatch(action.target_app, glob):
                    return Denied(
                        f"{action.target_app} is deny-listed "
                        f"(matched {glob!r})")
        # 2. explicit profile rules, first match wins
        for rule in self._profile.rules:
            if rule.matches(action):
                if rule.effect == "deny":
                    return Denied(f"denied by profile rule for "
                                  f"{action.tool}")
                if rule.effect == "confirm":
                    return NeedsConfirmation(self._prompt(action))
                return Allowed()
        # 3. secret patterns in typed text
        if action.text is not None:
            for pattern in self._sec.secret_patterns:
                if re.search(pattern, action.text):
                    return NeedsConfirmation(
                        f"{action.tool} would type text matching a "
                        f"secret pattern. Allow?")
        # 4. class defaults
        if action.policy_class == "read":
            return Allowed()
        if action.policy_class == "act":
            if self._profile.confirm_acts:
                return NeedsConfirmation(self._prompt(action))
            return Allowed()
        if self._profile.allow_sensitive:
            return Allowed()
        return NeedsConfirmation(self._prompt(action))

    async def confirm(self, prompt: str,
                      action: ActionDescriptor) -> bool:
        return await self._hook(prompt, action)

    @staticmethod
    def _prompt(action: ActionDescriptor) -> str:
        target = f" on {action.target_app}" if action.target_app else ""
        return f"Allow the agent to run {action.tool}{target}?"


class AllowAllPermissions:
    """M1 stub, kept for tests and headless setups."""

    def authorize(self, action: ActionDescriptor):
        return Allowed()

    async def confirm(self, prompt: str,
                      action: ActionDescriptor) -> bool:
        return True
```

- [ ] **Step 4: Update the dispatcher**

In `src/hands/dispatcher.py`:
- `__init__` gains `frontmost_provider=None` kwarg (store as `self._frontmost`), plus `self._recent_actions: deque[float] = deque()` (`from collections import deque`).
- In the preflight phase (after the kill-switch check), for non-read tools:

```python
            if spec.policy_class != "read":
                self._enforce_rate_limit()
```

with:

```python
    def _enforce_rate_limit(self) -> None:
        now = time.monotonic()
        window = self._recent_actions
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self._config.security.max_actions_per_s:
            raise PolicyDeniedError(
                "action rate limit exceeded",
                details={"max_actions_per_s":
                         self._config.security.max_actions_per_s},
                remediation="rate limit exceeded; slow down and retry")
        window.append(now)
```

- In the policy phase, build the richer descriptor and handle confirmation:

```python
            policy_class = spec.policy_class
            if spec.escalate is not None and spec.escalate(args):
                policy_class = "sensitive"
            target_app = self._frontmost() if self._frontmost else None
            action = ActionDescriptor(tool_name, policy_class,
                                      target_app=target_app,
                                      text=getattr(args, "text", None))
            decision = self._permissions.authorize(action)
            if isinstance(decision, NeedsConfirmation):
                if await self._permissions.confirm(decision.prompt,
                                                   action):
                    decision = Allowed()
                else:
                    decision = Denied(f"user declined: {tool_name}")
            decision.raise_if_denied()
```

(import `Allowed`, `Denied`, `NeedsConfirmation` from `.permissions`.)

- [ ] **Step 5: Wire the container**

In `Container.build`, replace `self.permissions = AllowAllPermissions()` with:

```python
        self.permissions = PermissionEngine(
            load_profile(config), make_confirm_hook(config), config)
```

and pass to the dispatcher:

```python
        self.dispatcher = Dispatcher(
            self.registry, self.permissions, self.state, self.audit,
            self.metrics, config,
            frontmost_provider=lambda: next(
                (a.bundle_id or a.name
                 for a in self.driver.running_apps() if a.frontmost),
                None))
```

**Note:** with the `default` profile, `clipboard_get` and escalated calls now require confirmation, and on non-macOS the default hook denies. M1's e2e/dispatcher tests that call only read/act tools are unaffected. Any existing test that dispatches a sensitive tool should set `cfg.security.profile = "trusted"` or inject `AllowAllPermissions`.

- [ ] **Step 6: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_permissions.py tests/unit/test_dispatcher.py -q` then `uv run pytest -q`
Expected: all pass.

---

### Task 9: Hash-chained audit log and `hands audit verify`

**Files:**
- Modify: `src/hands/audit.py`, `src/hands/cli.py`
- Test: `tests/unit/test_audit.py` (extend M1 file)

**Interfaces:**
- Consumes: M1 `AuditLogger(config)` interface (`record(event: dict)`, `flush()`); M1 CLI structure.
- Produces:
  - Line format becomes `{"event": <event>, "prev_hash": <hex|"">, "hash": sha256(prev_hash + canonical_json(event))}` where `canonical_json` = `json.dumps(event, sort_keys=True, separators=(",", ":"))`. The logger seeds `prev_hash` from the last line of an existing file (append across restarts keeps the chain).
  - `AuditLogger.verify_chain(path: Path) -> tuple[bool, int | None]` staticmethod — `(True, None)` or `(False, first_bad_line_number)` (1-based).
  - CLI: `hands audit verify [--path PATH]` — prints `audit chain OK (N lines)` and exits 0, or `audit chain BROKEN at line N` and exits 1.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_audit.py`:

```python
import json

from hands.audit import AuditLogger


def _logger(tmp_path):
    from hands.config import HandsConfig
    cfg = HandsConfig()
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    return cfg, AuditLogger(cfg)


def test_chain_verifies(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"tool": "mouse_move", "n": i})
    log.flush()
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is True and bad is None


def test_chain_survives_restart(tmp_path):
    cfg, log = _logger(tmp_path)
    log.record({"n": 1})
    log.flush()
    log2 = AuditLogger(cfg)          # new process, same file
    log2.record({"n": 2})
    log2.flush()
    ok, _ = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok


def test_tampering_detected(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"n": i})
    log.flush()
    lines = cfg.security.audit_path.read_text().splitlines()
    doctored = json.loads(lines[1])
    doctored["event"]["n"] = 999
    lines[1] = json.dumps(doctored)
    cfg.security.audit_path.write_text("\n".join(lines) + "\n")
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is False and bad == 2


def test_truncation_detected(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"n": i})
    log.flush()
    lines = cfg.security.audit_path.read_text().splitlines()
    cfg.security.audit_path.write_text("\n".join(lines[:1]) + "\n")
    log2 = AuditLogger(cfg)
    log2.record({"n": "after-truncation"})
    log2.flush()
    ok, _ = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok  # truncation from the END re-seeds cleanly...
    # ...but removing a MIDDLE line breaks the chain:
    lines = cfg.security.audit_path.read_text().splitlines()
    cfg.security.audit_path.write_text(
        "\n".join([lines[0], lines[-1]]) + "\n")
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_audit.py -q`
Expected: FAIL — `verify_chain` missing / format mismatch.

- [ ] **Step 3: Implement** — rewrite `src/hands/audit.py`:

```python
"""Append-only, hash-chained JSONL audit log (DESIGN §13.6).
line.hash = sha256(prev_hash + canonical_json(event)); tampering or
mid-file deletion breaks the chain."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      default=str)


class AuditLogger:
    def __init__(self, config) -> None:
        self._path: Path = config.security.audit_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prev = self._seed_prev()
        self._fh = self._path.open("a", encoding="utf-8")

    def _seed_prev(self) -> str:
        if not self._path.exists():
            return ""
        last = ""
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return ""
        try:
            return json.loads(last)["hash"]
        except (json.JSONDecodeError, KeyError):
            return ""

    def record(self, event: dict) -> None:
        body = _canonical(event)
        digest = hashlib.sha256(
            (self._prev + body).encode()).hexdigest()
        self._fh.write(json.dumps(
            {"event": event, "prev_hash": self._prev, "hash": digest},
            default=str) + "\n")
        self._prev = digest

    def flush(self) -> None:
        self._fh.flush()

    @staticmethod
    def verify_chain(path: Path) -> tuple[bool, int | None]:
        prev = ""
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    expected = hashlib.sha256(
                        (obj["prev_hash"]
                         + _canonical(obj["event"])).encode()).hexdigest()
                    if obj["hash"] != expected:
                        return False, lineno
                    if obj["prev_hash"] != prev:
                        return False, lineno
                    prev = obj["hash"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    return False, lineno
        return True, None
```

**Note:** `test_truncation_detected` shows the honest limit — truncating the *tail* re-seeds cleanly (documented in DESIGN §13.6 as detectable only against an external copy of the last hash); deleting a middle line is always detected. If M1's audit tests asserted the old flat-JSONL format, update them to read `json.loads(line)["event"]`.

Add the CLI subcommand in `src/hands/cli.py` (follow the existing `serve`/`doctor` argparse structure):

```python
    audit_p = sub.add_parser("audit", help="audit log utilities")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd", required=True)
    verify_p = audit_sub.add_parser("verify", help="verify hash chain")
    verify_p.add_argument("--path", type=Path, default=None)
```

and in the command handling:

```python
    if args.command == "audit" and args.audit_cmd == "verify":
        from .audit import AuditLogger
        from .config import load_config
        path = args.path or load_config().security.audit_path
        ok, bad = AuditLogger.verify_chain(path)
        if ok:
            n = sum(1 for line in path.open() if line.strip())
            print(f"audit chain OK ({n} lines)")
            return 0
        print(f"audit chain BROKEN at line {bad}")
        return 1
```

- [ ] **Step 4: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_audit.py -q` then `uv run pytest -q`
Expected: all pass.

---

### Task 10: Metrics histograms, OS permission status, CLI diagnostics

**Files:**
- Modify: `src/hands/metrics.py`, `src/hands/dispatcher.py`, `src/hands/driver/base.py`, `src/hands/driver/fake.py`, `src/hands/cli.py`
- Test: `tests/unit/test_metrics.py` (extend), `tests/unit/test_fake_driver.py` (append), `tests/unit/test_cli.py` (extend or create)

**Interfaces:**
- Consumes: M1 `Metrics` (`inc`, `snapshot`), M1 CLI.
- Produces:
  - `Metrics.observe(name: str, value: float, **labels)` — histogram samples, capped at the most recent 1000 per (name, labels) series; `snapshot()` now returns `{"counters": {...}, "histograms": {series: {"count", "sum", "p50", "p95"}}}`.
  - Dispatcher records `tool_seconds` per dispatch (label `tool`) in both success and error paths.
  - `OSPermissions(screen_recording: bool, accessibility: bool)` frozen dataclass in `hands.driver.base`; `Driver.permissions() -> OSPermissions`; `FakeDriver` returns `(True, True)`, overridable via `set_permissions(screen_recording=..., accessibility=...)`.
  - CLI: `hands doctor --metrics` appends the metrics snapshot; `hands permissions` prints both TCC grants with System Settings deep links (`x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture` / `?Privacy_Accessibility`), exit 0 if both granted else 1.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_metrics.py`:

```python
def test_histogram_observe_and_snapshot():
    from hands.metrics import Metrics
    m = Metrics()
    for v in [0.010, 0.020, 0.030, 0.040, 0.100]:
        m.observe("tool_seconds", v, tool="screenshot")
    snap = m.snapshot()
    series = snap["histograms"]["tool_seconds{tool=screenshot}"]
    assert series["count"] == 5
    assert 0.02 <= series["p50"] <= 0.04
    assert series["p95"] <= 0.1


def test_histogram_bounded():
    from hands.metrics import Metrics
    m = Metrics()
    for i in range(2000):
        m.observe("x", float(i))
    assert m.snapshot()["histograms"]["x"]["count"] == 1000
```

Append to `tests/unit/test_fake_driver.py`:

```python
from hands.driver.base import OSPermissions


def test_fake_permissions():
    drv = FakeDriver()
    assert drv.permissions() == OSPermissions(True, True)
    drv.set_permissions(screen_recording=False)
    assert drv.permissions().screen_recording is False
```

`tests/unit/test_cli.py` (create if M1 didn't):

```python
from hands.cli import main


def test_permissions_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("HANDS_DRIVER", "fake")
    assert main(["permissions"]) == 0
    out = capsys.readouterr().out
    assert "screen_recording" in out and "accessibility" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics.py tests/unit/test_fake_driver.py tests/unit/test_cli.py -q`
Expected: FAIL — `observe`/`permissions` missing.

- [ ] **Step 3: Implement**

`src/hands/metrics.py` — add to `Metrics`:

```python
    MAX_SAMPLES = 1000

    def observe(self, name: str, value: float, **labels) -> None:
        key = self._series_key(name, labels)
        samples = self._histograms.setdefault(key, [])
        samples.append(value)
        if len(samples) > self.MAX_SAMPLES:
            del samples[: len(samples) - self.MAX_SAMPLES]

    @staticmethod
    def _series_key(name: str, labels: dict) -> str:
        if not labels:
            return name
        inner = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{inner}}}"
```

(`self._histograms: dict[str, list[float]] = {}` in `__init__`; keep the M1 counter behavior, and reuse `_series_key` for counters if M1 formatted differently — keep M1's counter key format to avoid breaking its tests.) `snapshot()` returns:

```python
    def snapshot(self) -> dict:
        histograms = {}
        for key, samples in self._histograms.items():
            s = sorted(samples)
            n = len(s)
            histograms[key] = {
                "count": n,
                "sum": sum(s),
                "p50": s[int(0.50 * (n - 1))],
                "p95": s[int(0.95 * (n - 1))],
            }
        return {"counters": dict(self._counters),
                "histograms": histograms}
```

(If M1's `snapshot()` returned the counters dict directly, update M1's assertions to read `snapshot()["counters"]`.)

`src/hands/dispatcher.py` — in the success path (phase 7) and in the `except HandsError` path, add:

```python
            self._metrics.observe("tool_seconds",
                                  time.monotonic() - started,
                                  tool=tool_name)
```

`src/hands/driver/base.py`:

```python
@dataclass(frozen=True, slots=True)
class OSPermissions:
    screen_recording: bool
    accessibility: bool
```

plus `def permissions(self) -> OSPermissions: ...` on the protocol. `FakeDriver`:

```python
    def set_permissions(self, *, screen_recording: bool | None = None,
                        accessibility: bool | None = None) -> None:
        cur = getattr(self, "_permissions", OSPermissions(True, True))
        self._permissions = OSPermissions(
            cur.screen_recording if screen_recording is None
            else screen_recording,
            cur.accessibility if accessibility is None else accessibility)

    def permissions(self) -> OSPermissions:
        return getattr(self, "_permissions", OSPermissions(True, True))
```

`src/hands/cli.py` — add the `permissions` subcommand and a `--metrics` flag on `doctor`:

```python
    perm_p = sub.add_parser("permissions", help="show TCC grant status")
```

handler:

```python
    if args.command == "permissions":
        from .config import load_config
        from .container import Container
        container = Container.build(load_config())
        perms = container.driver.permissions()
        print(f"screen_recording: "
              f"{'granted' if perms.screen_recording else 'MISSING'}")
        print("  grant via: x-apple.systempreferences:"
              "com.apple.preference.security?Privacy_ScreenCapture")
        print(f"accessibility:    "
              f"{'granted' if perms.accessibility else 'MISSING'}")
        print("  grant via: x-apple.systempreferences:"
              "com.apple.preference.security?Privacy_Accessibility")
        return 0 if (perms.screen_recording
                     and perms.accessibility) else 1
```

For `doctor`, add `doctor_p.add_argument("--metrics", action="store_true")` and, in its handler, `if args.metrics: print(json.dumps(container.metrics.snapshot(), indent=2))`.

- [ ] **Step 4: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_metrics.py tests/unit/test_fake_driver.py tests/unit/test_cli.py -q` then `uv run pytest -q`
Expected: all pass.

---

### Task 11: macOS driver — clipboard, windows, apps, AX, TCC, secure input

**Files:**
- Modify: `pyproject.toml` (macos extra), `src/hands/driver/macos.py`
- Test: `tests/contract/test_macos_m3.py`

**Interfaces:**
- Consumes: all M3 driver-surface signatures (Tasks 1, 3, 5, 7, 10).
- Produces: `MacOSDriver` implementations of `clipboard_read/write`, `secure_input_active`, `list_windows`, `window_perform`, `running_apps`, `launch_app`, `activate_app`, `terminate_app`, `ax_tree`, `permissions`.

- [ ] **Step 1: Add dependencies** — extend the `macos` extra in `pyproject.toml`:

```toml
    "pyobjc-framework-Cocoa>=10.2; sys_platform == 'darwin'",
    "pyobjc-framework-ApplicationServices>=10.2; sys_platform == 'darwin'",
```

Run: `uv sync --extra macos`. Expected: resolves.

- [ ] **Step 2: Write the failing contract tests** — `tests/contract/test_macos_m3.py`:

```python
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
```

Run: `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract/test_macos_m3.py -q`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Implement** — add to `src/hands/driver/macos.py`:

```python
    # --- clipboard (DESIGN §4.7) -----------------------------------------
    def clipboard_read(self) -> ClipboardContent:
        from AppKit import (
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
        )
        pb = NSPasteboard.generalPasteboard()
        text = pb.stringForType_(NSPasteboardTypeString)
        if text is not None:
            return ClipboardContent("text", text=str(text))
        data = pb.dataForType_(NSPasteboardTypePNG)
        if data is not None:
            return ClipboardContent("image", image_png=bytes(data))
        return ClipboardContent("empty")

    def clipboard_write(self, content: ClipboardContent) -> None:
        from AppKit import (
            NSData,
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
        )
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        if content.kind == "text" and content.text is not None:
            pb.setString_forType_(content.text, NSPasteboardTypeString)
        elif content.kind == "image" and content.image_png is not None:
            pb.setData_forType_(
                NSData.dataWithBytes_length_(content.image_png,
                                             len(content.image_png)),
                NSPasteboardTypePNG)

    def secure_input_active(self) -> bool:
        import ctypes
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon")
        return bool(carbon.IsSecureEventInputEnabled())

    # --- windows (DESIGN §4.8) -------------------------------------------
    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionAll,
            kCGWindowListOptionOnScreenOnly,
        )
        opts = kCGWindowListExcludeDesktopElements
        opts |= (kCGWindowListOptionOnScreenOnly if on_screen_only
                 else kCGWindowListOptionAll)
        raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) or []
        apps = {a.pid: a for a in self.running_apps()}
        front_pid = next((a.pid for a in apps.values() if a.frontmost),
                         None)
        out: list[WindowInfo] = []
        for w in raw:
            if w.get("kCGWindowLayer", 0) != 0:
                continue                    # skip menubar/dock layers
            pid = int(w["kCGWindowOwnerPID"])
            b = w["kCGWindowBounds"]
            app = apps.get(pid)
            out.append(WindowInfo(
                window_ref=f"{pid}:{int(w['kCGWindowNumber'])}",
                app_name=str(w.get("kCGWindowOwnerName", "")),
                bundle_id=app.bundle_id if app else None,
                pid=pid,
                title=str(w.get("kCGWindowName", "") or ""),
                bounds=Region(float(b["X"]), float(b["Y"]),
                              float(b["Width"]), float(b["Height"])),
                focused=(pid == front_pid),
                minimized=not w.get("kCGWindowIsOnscreen", True)))
        return out

    def _ax_window_for_ref(self, window_ref: str):
        """Resolve 'pid:number' to an AXUIElement window by title+bounds
        proximity (AX has no CGWindowNumber bridge; DESIGN §4.8)."""
        import ApplicationServices as AS
        pid = int(window_ref.split(":")[0])
        target = next((w for w in self.list_windows(False)
                       if w.window_ref == window_ref), None)
        if target is None:
            raise TargetNotFoundError(f"window {window_ref} not found")
        app_el = AS.AXUIElementCreateApplication(pid)
        err, windows = AS.AXUIElementCopyAttributeValue(
            app_el, AS.kAXWindowsAttribute, None)
        if err != 0 or not windows:
            raise TargetNotFoundError(
                f"no AX windows for pid {pid}",
                details={"ax_error": int(err)})
        for el in windows:
            _, title = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXTitleAttribute, None)
            if str(title or "") == target.title:
                return el, target
        return windows[0], target        # single-window fallback

    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None:
        import ApplicationServices as AS
        import Quartz
        el, info = self._ax_window_for_ref(window_ref)
        if action in ("move", "resize", "maximize"):
            if action == "maximize":
                bounds = self.displays()[0].bounds_pt
            if action in ("move", "maximize"):
                point = Quartz.CGPoint(bounds.x, bounds.y)
                value = AS.AXValueCreate(AS.kAXValueCGPointType, point)
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXPositionAttribute, value)
            if action in ("resize", "maximize"):
                size = Quartz.CGSize(bounds.width, bounds.height)
                value = AS.AXValueCreate(AS.kAXValueCGSizeType, size)
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXSizeAttribute, value)
        elif action in ("minimize", "unminimize"):
            AS.AXUIElementSetAttributeValue(
                el, AS.kAXMinimizedAttribute,
                action == "minimize")
        elif action == "raise":
            self.activate_app(info.pid)
            AS.AXUIElementPerformAction(el, AS.kAXRaiseAction)
        elif action == "close":
            err, button = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXCloseButtonAttribute, None)
            if err != 0 or button is None:
                raise DriverError(f"window {window_ref} has no close "
                                  f"button (ax_error={int(err)})")
            AS.AXUIElementPerformAction(button, AS.kAXPressAction)
        else:
            raise InvalidArgsError(f"unknown window action: {action}")

    # --- apps (DESIGN §4.9) ------------------------------------------------
    def running_apps(self) -> list[AppInfo]:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        out = []
        for a in ws.runningApplications():
            if a.activationPolicy() != 0:      # regular apps only
                continue
            out.append(AppInfo(
                bundle_id=str(a.bundleIdentifier() or "") or None,
                name=str(a.localizedName() or ""),
                pid=int(a.processIdentifier()),
                frontmost=(front is not None
                           and a.processIdentifier()
                           == front.processIdentifier())))
        return out

    def launch_app(self, ident: str) -> AppInfo:
        import subprocess
        import time
        for a in self.running_apps():
            if ident in ((a.bundle_id or ""), a.name):
                self.activate_app(a.pid)
                return AppInfo(a.bundle_id, a.name, a.pid, True)
        flag = "-b" if "." in ident else "-a"
        proc = subprocess.run(["/usr/bin/open", flag, ident],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise TargetNotFoundError(
                f"cannot launch {ident}: {proc.stderr.strip()}")
        deadline = time.time() + 15
        while time.time() < deadline:
            for a in self.running_apps():
                if ident in ((a.bundle_id or ""), a.name):
                    return a
            time.sleep(0.2)
        raise DriverError(f"{ident} did not appear in running apps")

    def activate_app(self, pid: int) -> None:
        from AppKit import (
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )
        app = NSRunningApplication.\
            runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            raise TargetNotFoundError(f"pid {pid} not running")
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    def terminate_app(self, pid: int, force: bool) -> None:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.\
            runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            raise TargetNotFoundError(f"pid {pid} not running")
        if force:
            app.forceTerminate()
        else:
            app.terminate()

    # --- AX tree (DESIGN §5.15) --------------------------------------------
    def ax_tree(self, pid: int | None, max_depth: int) -> AXNode:
        import ApplicationServices as AS
        if not AS.AXIsProcessTrusted():
            raise PermissionMissingError(
                "Accessibility permission missing",
                remediation="x-apple.systempreferences:com.apple."
                            "preference.security?Privacy_Accessibility")
        if pid is None:
            front = next((a for a in self.running_apps()
                          if a.frontmost), None)
            if front is None:
                raise TargetNotFoundError("no frontmost app")
            pid = front.pid
        root = AS.AXUIElementCreateApplication(pid)

        def attr(el, name):
            err, value = AS.AXUIElementCopyAttributeValue(el, name, None)
            return value if err == 0 else None

        def walk(el, depth: int) -> AXNode:
            role = str(attr(el, AS.kAXRoleAttribute) or "AXUnknown")
            title = attr(el, AS.kAXTitleAttribute)
            value = attr(el, AS.kAXValueAttribute)
            region = None
            pos = attr(el, AS.kAXPositionAttribute)
            size = attr(el, AS.kAXSizeAttribute)
            if pos is not None and size is not None:
                ok_p, point = AS.AXValueGetValue(
                    pos, AS.kAXValueCGPointType, None)
                ok_s, sz = AS.AXValueGetValue(
                    size, AS.kAXValueCGSizeType, None)
                if ok_p and ok_s:
                    region = Region(point.x, point.y,
                                    sz.width, sz.height)
            err, actions = AS.AXUIElementCopyActionNames(el, None)
            children: tuple[AXNode, ...] = ()
            if depth > 1:
                kids = attr(el, AS.kAXChildrenAttribute) or []
                children = tuple(walk(k, depth - 1) for k in kids)
            return AXNode(role,
                          str(title) if title is not None else None,
                          str(value) if value is not None else None,
                          region,
                          tuple(str(a) for a in (actions or [])),
                          children)

        return walk(root, max_depth)

    # --- TCC (DESIGN §4.19) --------------------------------------------------
    def permissions(self) -> OSPermissions:
        import ApplicationServices as AS
        from Quartz import CGPreflightScreenCaptureAccess
        return OSPermissions(
            screen_recording=bool(CGPreflightScreenCaptureAccess()),
            accessibility=bool(AS.AXIsProcessTrusted()))
```

Imports at the top of `macos.py`: extend the existing block with `AppInfo`, `ClipboardContent`, `WindowInfo` from `..types`, `AXNode`, `OSPermissions` from `.base`, and `InvalidArgsError`, `PermissionMissingError`, `TargetNotFoundError` from `..errors`.

- [ ] **Step 4: Run contract tests** (on macOS with grants)

Run: `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q`
Expected: all pass (M1/M2 contract suites included).

- [ ] **Step 5: Verify**

Run: `uv run pytest -q` (any OS) and `uv run hands doctor && uv run hands permissions` (macOS).
Expected: suite green; CLIs report driver, 22 tools, and TCC status.

---

## Plan completion criteria

- `uv run pytest -q` green on any OS.
- On macOS: `HANDS_CONTRACT_MACOS=1 uv run pytest -q` green; `hands permissions` reports both grants; `hands audit verify` passes on a fresh session log; `hands doctor --metrics` prints a snapshot.
- 21 tools registered: M2's 11 + `clipboard_get/set/paste`, `window_list/focus/manage`, `app_open/close/list`, `get_ui_tree`.
- Policy behavior observable over MCP: `clipboard_get` under the `default` profile requires confirmation (auto-denied headless); deny-listed frontmost app blocks acting tools; >10 acting calls/s are rate-limited; typing while secure input is active is refused.
- Audit lines are hash-chained; tampering with any middle line is detected.
- Nothing committed to git (user instruction).
