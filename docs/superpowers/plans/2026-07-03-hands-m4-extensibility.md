# Hands Milestone 4 — Extensibility & Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The plugin system (entry-point discovery, stable `PluginContext` API), the `execute_sequence` batching tool with guard conditions, a real-macOS e2e suite driving a deterministic Tk fixture app, and performance/stress suites that enforce the DESIGN §14 budgets.

**Architecture:** Plugins register through the same `ToolSpec` machinery as built-ins (no side door around the dispatcher); `execute_sequence` reuses the dispatcher's validate/execute path via a new lock-free `call_unlocked` entry point (the sequence itself holds the action lock); e2e tests exercise the full stack (container → dispatcher → macOS driver → real Tk app) using OCR for both targeting and assertions.

**Tech Stack:** Same as M1–M3, plus `pytest-benchmark` (dev). Fixture app uses stdlib `tkinter` (no new runtime deps).

## Milestone map (context, not tasks)

- **M1–M3 (done, prerequisites):** full framework, 21 tools, fake + macOS drivers, policy/audit/metrics.
- **M4 (this plan):** plugin system, `execute_sequence` (22 tools), e2e fixture app + suite, perf + stress suites.

## Global Constraints

- **M1, M2, and M3 plans must be fully implemented and green (`uv run pytest -q`) before starting.**
- Python `>=3.12`; `src/` layout; package `hands`; managed with `uv`.
- **No git commits for now (user instruction, 2026-07-03).** Tasks end with a "Verify" step running the full test suite instead of a commit. When the user lifts this, commit once per completed task with `feat:`/`test:` prefixes.
- `stdout` is reserved for the MCP transport in `serve` mode; logging to `stderr`.
- Pydantic argument models use `extra="forbid"`.
- `src/hands/plugins/api.py` is the **only** import surface plugin authors may use (DESIGN §6.4); it is semver-stable from this milestone on.
- A broken plugin must never take the server down: load failures log and skip (DESIGN §7.12).
- `execute_sequence` is a macro, not a planner: ≤ 20 steps, acting tools only, no nesting, authorized as a whole and per-step up front (DESIGN §5.16).
- e2e tests gated by `HANDS_E2E_MACOS=1`; perf/stress marked `@pytest.mark.perf` / `@pytest.mark.stress` and excluded from the default run via `addopts = "-m 'not perf and not stress'"`.

---

### Task 1: Plugin API and PluginManager

**Files:**
- Create: `src/hands/plugins/__init__.py`, `src/hands/plugins/api.py`
- Modify: `src/hands/config.py` (add `plugin_allowlist`), `src/hands/container.py`, `src/hands/server.py`
- Test: `tests/unit/test_plugins.py`

**Interfaces:**
- Consumes: `ToolRegistry`/`ToolSpec` (M1), `Container` (M1–M3 service attributes), `structlog`.
- Produces (in `hands.plugins.api` — the stable surface):
  - `PluginContext(registry: ToolRegistry, config: Mapping[str, Any], logger, services: Mapping[type, object])` with attribute access to `registry`, `config`, `logger` and `service(proto: type[T]) -> T` (raises `LookupError` for unknown protocols).
  - `HandsPlugin` runtime-checkable Protocol: `name: str`, `version: str`, `setup(ctx: PluginContext) -> None`, `teardown() -> None`.
- Produces (in `hands.plugins`):
  - `ENTRY_POINT_GROUP = "hands.plugins"`.
  - `PluginManager(ctx_factory: Callable[[HandsPlugin], PluginContext])` with `discover_and_load(allowlist: list[str] | None) -> None` (entry-point discovery; allowlist filtering; broken plugins logged and skipped), `loaded: list[HandsPlugin]`, `teardown_all() -> None` (reverse order, exceptions contained).
- Config: `SecurityConfig.plugin_allowlist: list[str] | None = None` (None = all plugins allowed; a list refuses anything not on it — DESIGN §13.7).
- Container: `self.plugins = PluginManager(self._plugin_ctx)` built last, with `_plugin_ctx(plugin)` returning a `PluginContext` whose `services` map covers `Driver`, `ScreenshotService`, `OCRService`, `MouseService`, `KeyboardService`, `ClipboardService`, `WindowService`, `AppService`, `Waiter`, `VerificationEngine`, `StateManager`, and whose `config` is `config.model_dump().get("plugins", {}).get(plugin.name, {})` — add `plugins: dict[str, dict] = {}` to `HandsConfig` for per-plugin namespaces.
- Server: `run_server` calls `container.plugins.discover_and_load(config.security.plugin_allowlist)` before serving and `container.plugins.teardown_all()` in the existing `finally` (before `keyboard.release_all()`).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_plugins.py`:

```python
from types import SimpleNamespace

import pytest

import hands.plugins as plugins_mod
from hands.config import HandsConfig
from hands.container import Container
from hands.plugins import ENTRY_POINT_GROUP, PluginManager
from hands.plugins.api import HandsPlugin, PluginContext
from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy
from hands.services.screenshot import ScreenshotService


class GoodPlugin:
    name, version = "good", "1.0.0"
    torn_down = False

    def setup(self, ctx: PluginContext) -> None:
        from pydantic import BaseModel

        class NoArgs(BaseModel, extra="forbid"):
            pass

        async def ping(args, ctx_):
            return {"pong": True}

        ctx.registry.register(ToolSpec(
            "plugin_ping", "plugin-provided tool", NoArgs, ping,
            "read", RetryPolicy.read(), idempotent=True))
        # DI lookup works
        assert isinstance(ctx.service(ScreenshotService),
                          ScreenshotService)

    def teardown(self) -> None:
        GoodPlugin.torn_down = True


class BrokenPlugin:
    name, version = "broken", "1.0.0"

    def setup(self, ctx) -> None:
        raise RuntimeError("boom")

    def teardown(self) -> None:
        pass


class _FakeEntryPoint:
    def __init__(self, name, cls):
        self.name = name
        self._cls = cls

    def load(self):
        return self._cls


def _patch_entry_points(monkeypatch, *eps):
    monkeypatch.setattr(
        plugins_mod, "entry_points",
        lambda group: list(eps) if group == ENTRY_POINT_GROUP else [])


@pytest.fixture
def container():
    cfg = HandsConfig()
    cfg.driver = "fake"
    return Container.build(cfg)


def test_plugin_registers_tool_and_gets_services(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    assert container.registry.get("plugin_ping").name == "plugin_ping"
    assert len(container.plugins.loaded) == 1


def test_broken_plugin_is_skipped_not_fatal(monkeypatch, container):
    _patch_entry_points(monkeypatch,
                        _FakeEntryPoint("broken", BrokenPlugin),
                        _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    assert [p.name for p in container.plugins.loaded] == ["good"]


def test_allowlist_refuses_unlisted(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(["other-plugin"])
    assert container.plugins.loaded == []


def test_teardown_all_reverse_and_contained(monkeypatch, container):
    _patch_entry_points(monkeypatch, _FakeEntryPoint("good", GoodPlugin))
    container.plugins.discover_and_load(None)
    GoodPlugin.torn_down = False
    container.plugins.teardown_all()
    assert GoodPlugin.torn_down is True


def test_protocol_runtime_checkable():
    assert isinstance(GoodPlugin(), HandsPlugin)


def test_unknown_service_lookup_raises(container):
    ctx = container._plugin_ctx(GoodPlugin())

    class NotAService:
        pass

    with pytest.raises(LookupError):
        ctx.service(NotAService)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_plugins.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.plugins`.

- [ ] **Step 3: Implement `src/hands/plugins/api.py`**

```python
"""The ONLY stable import surface for plugin authors (DESIGN §6.4).
Semver-guarded from M4 on: additive changes only within a major version."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class PluginContext:
    def __init__(self, registry, config: Mapping[str, Any], logger,
                 services: Mapping[type, object]) -> None:
        self.registry = registry
        self.config = config
        self.logger = logger
        self._services = services

    def service(self, proto: type[T]) -> T:
        try:
            return self._services[proto]        # type: ignore[return-value]
        except KeyError:
            raise LookupError(
                f"no service registered for {proto.__name__}") from None


@runtime_checkable
class HandsPlugin(Protocol):
    name: str
    version: str

    def setup(self, ctx: PluginContext) -> None: ...
    def teardown(self) -> None: ...
```

- [ ] **Step 4: Implement `src/hands/plugins/__init__.py`**

```python
"""Entry-point plugin discovery (DESIGN §11)."""
from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points

import structlog

from .api import HandsPlugin, PluginContext

ENTRY_POINT_GROUP = "hands.plugins"
log = structlog.get_logger(__name__)


class PluginManager:
    def __init__(self, ctx_factory: Callable[[HandsPlugin],
                                             PluginContext]) -> None:
        self._ctx_factory = ctx_factory
        self.loaded: list[HandsPlugin] = []

    def discover_and_load(self, allowlist: list[str] | None) -> None:
        """A broken plugin logs and is skipped — it must never take the
        server down. With an allowlist, unknown entry points are refused
        (DESIGN §13.7)."""
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if allowlist is not None and ep.name not in allowlist:
                log.warning("plugin_skipped_not_allowlisted",
                            name=ep.name)
                continue
            try:
                plugin: HandsPlugin = ep.load()()
                plugin.setup(self._ctx_factory(plugin))
                self.loaded.append(plugin)
                log.info("plugin_loaded", name=plugin.name,
                         version=plugin.version)
            except Exception:
                log.exception("plugin_failed", name=ep.name)

    def teardown_all(self) -> None:
        for plugin in reversed(self.loaded):
            try:
                plugin.teardown()
            except Exception:
                log.exception("plugin_teardown_failed", name=plugin.name)
        self.loaded.clear()
```

- [ ] **Step 5: Wire config, container, server**

`src/hands/config.py`: add `plugin_allowlist: list[str] | None = None` to `SecurityConfig` and `plugins: dict[str, dict] = {}` to `HandsConfig`.

`src/hands/container.py` — at the end of `Container.build`, after the dispatcher:

```python
        self.plugins = PluginManager(self._plugin_ctx)
```

and the method on `Container`:

```python
    def _plugin_ctx(self, plugin) -> "PluginContext":
        from .driver.base import Driver
        from .plugins.api import PluginContext
        services: dict[type, object] = {
            Driver: self.driver,
            StateManager: self.state,
            ScreenshotService: self.screenshots,
            OCRService: self.ocr,
            MouseService: self.mouse,
            KeyboardService: self.keyboard,
            ClipboardService: self.clipboard,
            WindowService: self.windows,
            AppService: self.apps,
            Waiter: self.waiter,
            VerificationEngine: self.verification,
        }
        import structlog
        return PluginContext(
            registry=self.registry,
            config=self.config.plugins.get(plugin.name, {}),
            logger=structlog.get_logger(f"hands.plugin.{plugin.name}"),
            services=services)
```

(the service classes are already imported at the top of `container.py`; add any missing ones.)

`src/hands/server.py` — in `run_server`, before entering the stdio loop:

```python
    container.plugins.discover_and_load(
        config.security.plugin_allowlist)
```

and in the existing `finally`, before `keyboard.release_all()`:

```python
        container.plugins.teardown_all()
```

Document the author-facing contract in `docs/plugins.md` (short page: entry-point TOML snippet from DESIGN §11.1, the `HandsPlugin` protocol, what `ctx.service` offers, the allowlist).

- [ ] **Step 6: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_plugins.py -q` then `uv run pytest -q`
Expected: all pass.

---

### Task 2: `execute_sequence`

**Files:**
- Modify: `src/hands/dispatcher.py` (add `call_unlocked`)
- Create: `src/hands/tools/sequence.py`
- Modify: `src/hands/tools/__init__.py`, `src/hands/container.py` (no new services — `sequence` needs `container.dispatcher`, `container.registry`, `container.permissions`, `container.waiter`, `container.screenshots`; ensure `register_builtin_tools` runs where those exist. **Note:** in M1 `register_builtin_tools` runs *before* the dispatcher is built — move tool registration *after* `self.dispatcher = ...`/`frontmost_provider` wiring in `Container.build`; the registry object is created earlier so nothing else changes.)
- Test: `tests/unit/test_dispatcher.py` (append), `tests/unit/test_tools_sequence.py`

**Interfaces:**
- Consumes: dispatcher pipeline, `ActionDescriptor`/decisions (M3 Task 8), `Waiter` (M2/M3), `ScreenshotService`.
- Produces:
  - `Dispatcher.call_unlocked(tool_name: str, raw_args: dict, ctx: Any = None) -> dict` — validate → execute-with-retry → observe (record action, mark dirty) → audit/metrics, **no action lock, no policy phase** (callers pre-authorize). Always returns an envelope.
  - Tool `execute_sequence` (act, `RetryPolicy.none()`, I:no):
    - Args: `steps: list[{tool: str, args: dict = {}, guard?: dict, guard_timeout_ms: int = 5000}]` (1–20), `stop_on_failure: bool = true`, `screenshot_after: bool = true`.
    - Up-front validation (all before step 1 executes): every tool exists, is not `execute_sequence`, and has `policy_class != "read"`; every step is authorized (escalation applied; `NeedsConfirmation` resolved via one `confirm` per step; any denial rejects the whole sequence).
    - Execution: per step, an unmet `guard` (a waiter condition dict) halts with its evidence; otherwise `call_unlocked`; failures halt when `stop_on_failure`.
    - Response: `{ok, results: [per-step envelopes or {skipped: true}], completed: int, halted_by?: "guard"|"failure", guard_evidence?, final_screenshot?}` (`final_screenshot` = `screenshot.meta()` + `image_b64`).

- [ ] **Step 1: Write the failing dispatcher test** — append to `tests/unit/test_dispatcher.py`:

```python
async def test_call_unlocked_skips_lock_and_policy(dispatcher_env):
    env = dispatcher_env

    class DenyEverything:
        def authorize(self, action):
            from hands.permissions import Denied
            return Denied("no")

    env.dispatcher._permissions = DenyEverything()
    # dispatch() is denied…
    res = await env.dispatcher.dispatch("mouse_move", {"x": 1, "y": 1})
    assert res["ok"] is False
    # …but call_unlocked bypasses policy (callers pre-authorize)
    res = await env.dispatcher.call_unlocked("mouse_move",
                                             {"x": 2, "y": 2})
    assert res["ok"] is True


async def test_call_unlocked_still_validates_and_observes(dispatcher_env):
    env = dispatcher_env
    res = await env.dispatcher.call_unlocked("mouse_move", {"x": "bad"})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_ARGS"
    ok = await env.dispatcher.call_unlocked("mouse_move",
                                            {"x": 5, "y": 5})
    assert ok["ok"] is True
    assert env.state.screen_dirty is True
```

(Adapt attribute names to M1's fixture: it exposes the state manager; if not, assert via `dispatch("get_state", {})`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dispatcher.py -q`
Expected: FAIL — `AttributeError: call_unlocked`.

- [ ] **Step 3: Implement `call_unlocked`** — in `src/hands/dispatcher.py`, factor the shared tail out of `dispatch` (or add alongside; keep `dispatch` behavior identical):

```python
    async def call_unlocked(self, tool_name: str, raw_args: dict,
                            ctx: Any = None) -> dict:
        """Validate → execute → observe, WITHOUT the action lock or the
        policy phase. Only for callers that already hold the lock and
        pre-authorized every call (execute_sequence, DESIGN §5.16)."""
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        spec = self._registry.get(tool_name)
        try:
            try:
                args = spec.args_model.model_validate(raw_args)
            except ValidationError as e:
                raise InvalidArgsError(str(e),
                                       details={"errors": e.errors()})
            result = await execute_with_retry(
                lambda: spec.handler(args, ctx), spec.retry)
            self._state.record_action(ActionRecord.ok(
                request_id, tool_name, raw_args,
                time.monotonic() - started))
            if spec.policy_class != "read":
                self._state.mark_screen_dirty()
            self._audit.record({"request_id": request_id,
                                "tool": tool_name, "outcome": "ok",
                                "via": "sequence"})
            self._metrics.inc("tool_calls_total", tool=tool_name,
                              outcome="ok")
            return {"ok": True, **result}
        except HandsError as err:
            self._state.record_action(ActionRecord.failed(
                request_id, tool_name, raw_args, err))
            self._audit.record({"request_id": request_id,
                                "tool": tool_name, "outcome": err.code,
                                "via": "sequence"})
            self._metrics.inc("tool_calls_total", tool=tool_name,
                              outcome=err.code)
            return {"ok": False, "error": err.to_wire()}
```

(Match the audit/metrics call shapes to what M1's `dispatch` actually uses — same helpers, plus the `"via": "sequence"` field.)

Run: `uv run pytest tests/unit/test_dispatcher.py -q` — expected: PASS.

- [ ] **Step 4: Write the failing sequence-tool tests** — `tests/unit/test_tools_sequence.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.container import Container
from hands.driver.base import RawTextBox

pytestmark = pytest.mark.anyio


@pytest.fixture
def container():
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    return Container.build(cfg)


async def test_sequence_executes_steps_in_order(container):
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 10, "y": 10}},
            {"tool": "keyboard_type", "args": {"text": "hi"}},
        ]})
    assert res["ok"] is True
    assert res["completed"] == 2
    assert all(r["ok"] for r in res["results"])
    kinds = [e[0] for e in container.driver.pop_events()]
    assert kinds.index("mouse") < kinds.index("type")
    assert "image_b64" in res["final_screenshot"]


async def test_guard_failure_halts_with_evidence(container):
    container.driver.set_ocr_boxes([])       # guard text never appears
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 1, "y": 1}},
            {"tool": "mouse_move", "args": {"x": 2, "y": 2},
             "guard": {"type": "text_present", "text": "Ready"},
             "guard_timeout_ms": 30},
        ],
        "screenshot_after": False})
    assert res["ok"] is True                # sequence answered, not erred
    assert res["completed"] == 1
    assert res["halted_by"] == "guard"
    assert res["results"][1] == {"skipped": True}


async def test_step_failure_halts_when_stop_on_failure(container):
    # An out-of-bounds move raises InvalidArgsError from the service —
    # non-retryable, so the step fails deterministically. (A one-shot
    # fail_next injection would be consumed and the retry policy's second
    # attempt would succeed.)
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 99_999, "y": 5}},
            {"tool": "mouse_move", "args": {"x": 2, "y": 2}},
        ],
        "screenshot_after": False})
    assert res["ok"] is True               # sequence answered, not erred
    assert res["completed"] == 0
    assert res["halted_by"] == "failure"
    assert res["results"][0]["ok"] is False
    assert res["results"][0]["error"]["code"] == "INVALID_ARGS"
    assert res["results"][1] == {"skipped": True}


async def test_rejects_read_tools_nesting_and_unknown(container):
    for steps in (
        [{"tool": "screenshot"}],                       # read tool
        [{"tool": "execute_sequence", "args": {"steps": []}}],  # nested
        [{"tool": "no_such_tool"}],
    ):
        res = await container.dispatcher.dispatch(
            "execute_sequence", {"steps": steps})
        assert res["ok"] is False
        assert res["error"]["code"] == "INVALID_ARGS"


async def test_any_denied_step_rejects_whole_sequence(container):
    from hands.permissions import Denied

    class DenyTyping:
        def authorize(self, action):
            from hands.permissions import Allowed
            if action.tool.startswith("keyboard"):
                return Denied("no typing")
            return Allowed()

        async def confirm(self, prompt, action):
            return True

    container.dispatcher._permissions = DenyTyping()
    res = await container.dispatcher.dispatch("execute_sequence", {
        "steps": [
            {"tool": "mouse_move", "args": {"x": 1, "y": 1}},
            {"tool": "keyboard_type", "args": {"text": "hi"}},
        ]})
    assert res["ok"] is False
    assert res["error"]["code"] == "POLICY_DENIED"
    assert container.driver.pop_events() == []      # nothing ran
```

Run: `uv run pytest tests/unit/test_tools_sequence.py -q`
Expected: FAIL — `unknown tool: execute_sequence`.

- [ ] **Step 5: Implement `src/hands/tools/sequence.py`**

```python
"""execute_sequence: latency batching of pre-decided actions with guard
conditions (DESIGN §5.16). A macro, not a planner."""
from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from ..errors import InvalidArgsError, PolicyDeniedError
from ..permissions import ActionDescriptor, Denied, NeedsConfirmation
from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy


class StepArg(BaseModel, extra="forbid"):
    tool: str
    args: dict = {}
    guard: dict | None = None
    guard_timeout_ms: int = Field(default=5_000, ge=0, le=60_000)


class SequenceArgs(BaseModel, extra="forbid"):
    steps: list[StepArg] = Field(min_length=1, max_length=20)
    stop_on_failure: bool = True
    screenshot_after: bool = True


def register(registry: ToolRegistry, container) -> None:
    async def execute_sequence(args: SequenceArgs, ctx) -> dict:
        dispatcher = container.dispatcher
        permissions = container.permissions
        waiter = container.waiter
        shots = container.screenshots

        # -- validate every step up front (DESIGN §5.16) ------------------
        specs = []
        for i, step in enumerate(args.steps):
            if step.tool == "execute_sequence":
                raise InvalidArgsError(
                    f"step {i}: nested sequences are not allowed")
            spec = registry.get(step.tool)      # unknown -> INVALID_ARGS
            if spec.policy_class == "read":
                raise InvalidArgsError(
                    f"step {i}: read tool {step.tool!r} not allowed in a "
                    f"sequence — call it directly")
            specs.append(spec)

        # -- authorize the whole sequence and each step -------------------
        for i, (step, spec) in enumerate(zip(args.steps, specs)):
            try:
                validated = spec.args_model.model_validate(step.args)
            except Exception as e:
                raise InvalidArgsError(f"step {i} ({step.tool}): {e}")
            policy_class = spec.policy_class
            if spec.escalate is not None and spec.escalate(validated):
                policy_class = "sensitive"
            action = ActionDescriptor(
                step.tool, policy_class,
                text=getattr(validated, "text", None))
            decision = permissions.authorize(action)
            if isinstance(decision, NeedsConfirmation):
                if not await permissions.confirm(decision.prompt, action):
                    decision = Denied(f"user declined step {i}: "
                                      f"{step.tool}")
            if isinstance(decision, Denied):
                raise PolicyDeniedError(
                    f"sequence rejected: {decision.reason}",
                    details={"step": i, "tool": step.tool})

        # -- execute -------------------------------------------------------
        results: list[dict] = []
        completed = 0
        halted_by: str | None = None
        guard_evidence: dict | None = None
        for step in args.steps:
            if halted_by is not None:
                results.append({"skipped": True})
                continue
            if step.guard is not None:
                wait = await waiter.wait_for(step.guard,
                                             step.guard_timeout_ms)
                if not wait.met:
                    halted_by = "guard"
                    guard_evidence = wait.evidence
                    results.append({"skipped": True})
                    continue
            res = await dispatcher.call_unlocked(step.tool, step.args)
            results.append(res)
            if res.get("ok"):
                completed += 1
            elif args.stop_on_failure:
                halted_by = "failure"

        out: dict = {"results": results, "completed": completed}
        if halted_by is not None:
            out["halted_by"] = halted_by
        if guard_evidence is not None:
            out["guard_evidence"] = guard_evidence
        if args.screenshot_after:
            shot = await shots.capture(fresh=True)
            out["final_screenshot"] = {
                **shot.meta(),
                "image_b64": base64.b64encode(shot.data).decode()}
        return out

    registry.register(ToolSpec(
        "execute_sequence",
        "Run up to 20 PRE-DECIDED acting steps in one call (click, type, "
        "press...), each optionally gated by a guard condition (same "
        "schema as `wait`). A failed guard halts the sequence and returns "
        "evidence. Use only when you already know every step; this is a "
        "macro, not a planner.",
        SequenceArgs, execute_sequence, "act", RetryPolicy.none(),
        idempotent=False))
```

Register `sequence` last in `tools/__init__.py`, and apply the container-ordering note from **Files** above (registration moves after dispatcher construction).

- [ ] **Step 6: Run tests to verify they pass, then verify**

Run: `uv run pytest tests/unit/test_tools_sequence.py -q` then `uv run pytest -q`
Expected: all pass (tool total now 22).

---

### Task 3: e2e fixture app and real-macOS e2e suite

**Files:**
- Create: `tests/e2e/__init__.py` (empty), `tests/e2e/fixture_app.py`, `tests/e2e/test_fixture_flow.py`

**Interfaces:**
- Consumes: the full container over the real macOS driver; OCR for targeting and assertions.
- Produces: a deterministic Tk target app and one end-to-end scenario proving the perceive→act→verify loop on real hardware (DESIGN §12 e2e row).

- [ ] **Step 1: Implement `tests/e2e/fixture_app.py`** (the app comes first — the test needs it to exist):

```python
"""Deterministic Tk fixture app for e2e tests (DESIGN §12).
Big high-contrast text so Vision OCR is reliable at any scale."""
from __future__ import annotations

import tkinter as tk

FONT = ("Helvetica", 28, "bold")


def main() -> None:
    root = tk.Tk()
    root.title("Hands Fixture")
    root.geometry("640x420+120+120")
    root.configure(bg="white")

    count = tk.IntVar(value=0)
    counter = tk.Label(root, text="COUNT 0", font=FONT, bg="white",
                       fg="black")
    counter.pack(pady=16)

    def bump() -> None:
        count.set(count.get() + 1)
        counter.config(text=f"COUNT {count.get()}")

    tk.Button(root, text="INCREMENT", font=FONT, command=bump,
              height=2).pack(pady=8)

    entry = tk.Entry(root, font=FONT, width=18)
    entry.pack(pady=8)
    echo = tk.Label(root, text="ECHO", font=FONT, bg="white", fg="black")
    echo.pack(pady=8)
    entry.bind("<KeyRelease>",
               lambda e: echo.config(text=f"ECHO {entry.get()}"))

    root.mainloop()


if __name__ == "__main__":
    main()
```

Sanity-check by hand (optional, macOS): `uv run python tests/e2e/fixture_app.py` shows the window; close it.

- [ ] **Step 2: Write the e2e test** — `tests/e2e/test_fixture_flow.py`:

```python
"""Full-stack e2e on real macOS. Gated: HANDS_E2E_MACOS=1.
Requires Screen Recording + Accessibility; do not run while using the
machine — it moves your mouse."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("HANDS_E2E_MACOS") != "1"
        or sys.platform != "darwin",
        reason="real-desktop e2e is opt-in"),
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
    d = container.dispatcher

    # 1. the fixture window appears
    res = await _call(container, "wait", {
        "condition": {"type": "window_present",
                      "title": "Hands Fixture"},
        "timeout_ms": 15_000})
    assert res["met"], "fixture window never appeared"
    await _call(container, "window_focus",
                {"title_match": "Hands Fixture"})

    # 2. find and click INCREMENT (OCR-grounded targeting)
    found = await _call(container, "find_text", {"text": "INCREMENT"})
    assert found["matches"], "OCR could not find the button"
    center = found["matches"][0]["center"]
    await _call(container, "mouse_click",
                {"x": center["x"], "y": center["y"]})

    # 3. verify the counter advanced
    res = await _call(container, "wait", {
        "condition": {"type": "text_present", "text": "COUNT 1"},
        "timeout_ms": 5_000})
    assert res["met"], "counter did not increment"

    # 4. type into the entry and verify the echo
    found = await _call(container, "find_text", {"text": "ECHO"})
    echo_center = found["matches"][0]["center"]
    await _call(container, "mouse_click",
                {"x": echo_center["x"], "y": echo_center["y"] - 80})
    await _call(container, "keyboard_type", {"text": "abc"})
    res = await _call(container, "wait", {
        "condition": {"type": "text_present", "text": "ECHO abc"},
        "timeout_ms": 5_000})
    assert res["met"], "typed text did not echo"
```

(The `- 80` targets the entry directly above the echo label; the fixture's fixed geometry makes this stable. If flaky on a specific machine, click the entry via `get_ui_tree` instead — role `AXTextField`.)

- [ ] **Step 3: Run it** (macOS, permissions granted, hands off the mouse)

Run: `HANDS_E2E_MACOS=1 uv run pytest tests/e2e -q`
Expected: 1 passed (~10–20 s). On failure the messages say which stage broke (window / OCR / click / echo).

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: all pass; e2e skipped without the gate.

---

### Task 4: Performance benchmark suite

**Files:**
- Modify: `pyproject.toml` (dev dep + markers + addopts)
- Create: `tests/perf/__init__.py` (empty), `tests/perf/test_benchmarks.py`

**Interfaces:**
- Consumes: full container over the fake driver.
- Produces: trend-trackable benchmarks for the DESIGN §14 budgets that are meaningful off-macOS: dispatcher overhead, cached vs uncached screenshot, click latency. (Real capture/OCR latency is hardware-bound and covered by the nightly contract run.)

- [ ] **Step 1: Configure** — in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    # ...existing entries...
    "pytest-benchmark>=4.0",
]

[tool.pytest.ini_options]
# keep existing settings; add:
markers = [
    "perf: performance benchmarks (opt-in: -m perf)",
    "stress: soak/stress tests (opt-in: -m stress)",
]
addopts = "-m 'not perf and not stress'"
```

Run: `uv sync` then `uv run pytest -q` — expected: suite still green (markers excluded by default).

- [ ] **Step 2: Write the benchmarks** — `tests/perf/test_benchmarks.py`:

```python
"""Latency budgets on the fake driver (DESIGN §14). Run:
    uv run pytest tests/perf -m perf --benchmark-only
Budgets asserted loosely (2x headroom) to avoid CI-noise flakes; trends
are what matter (--benchmark-autosave)."""
import anyio
import pytest

from hands.config import HandsConfig
from hands.container import Container

pytestmark = pytest.mark.perf


@pytest.fixture
def container():
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    return Container.build(cfg)


def _run(coro_fn):
    return anyio.run(coro_fn)


def test_dispatch_overhead(benchmark, container):
    async def once():
        return await container.dispatcher.dispatch("get_state", {})

    result = benchmark(lambda: _run(once))
    assert result["ok"]
    assert benchmark.stats.stats.mean < 0.010   # 10 ms of pure pipeline


def test_screenshot_cached_vs_uncached(benchmark, container):
    async def cached():
        await container.dispatcher.dispatch("screenshot",
                                            {"fresh": True})
        return await container.dispatcher.dispatch("screenshot", {})

    result = benchmark(lambda: _run(cached))
    assert result["ok"] and result["cached"]


def test_click_latency(benchmark, container):
    cfg = container.config
    cfg.mouse.click_delay_ms = 0

    async def once():
        return await container.dispatcher.dispatch(
            "mouse_click", {"x": 100, "y": 100})

    result = benchmark(lambda: _run(once))
    assert result["ok"]
    assert benchmark.stats.stats.mean < 0.050   # DESIGN §14: ≤50 ms
```

- [ ] **Step 3: Run and verify**

Run: `uv run pytest tests/perf -m perf --benchmark-only -q`
Expected: 3 benchmarks pass with stats printed.
Run: `uv run pytest -q` — expected: perf excluded, suite green.

---

### Task 5: Stress / soak suite

**Files:**
- Create: `tests/stress/__init__.py` (empty), `tests/stress/test_soak.py`

**Interfaces:**
- Consumes: full container over the fake driver.
- Produces: leak detection over 10 000 dispatches (bounded state, DESIGN §14.7) and action-lock correctness under concurrent reads during a drag (DESIGN §12 stress row).

- [ ] **Step 1: Write the tests** — `tests/stress/test_soak.py`:

```python
"""Soak + concurrency stress (DESIGN §12). Run:
    uv run pytest tests/stress -m stress -q"""
import tracemalloc

import anyio
import pytest

from hands.config import HandsConfig
from hands.container import Container

pytestmark = [pytest.mark.stress, pytest.mark.anyio]


@pytest.fixture
def container():
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    cfg.security.max_actions_per_s = 1e9      # not testing the limiter
    cfg.mouse.click_delay_ms = 0
    return Container.build(cfg)


async def test_10k_actions_do_not_leak(container):
    d = container.dispatcher

    async def burst(n: int) -> None:
        for i in range(n):
            r = await d.dispatch("mouse_move",
                                 {"x": i % 1000, "y": i % 800})
            assert r["ok"]
            if i % 20 == 0:
                assert (await d.dispatch("screenshot",
                                         {"fresh": True}))["ok"]

    tracemalloc.start()
    await burst(1000)                          # warm up all caches
    baseline, _ = tracemalloc.get_traced_memory()
    await burst(9000)
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    growth_mb = (current - baseline) / 1e6
    assert growth_mb < 10, f"leaked {growth_mb:.1f} MB over 9k actions"
    # bounded state held (DESIGN §8.1)
    assert len(container.state.history(10_000)) \
        <= container.config.state.history_len


async def test_reads_during_drag_do_not_corrupt_events(container):
    d = container.dispatcher
    driver = container.driver
    driver.pop_events()
    read_results: list[bool] = []

    async def reader() -> None:
        for _ in range(25):
            r = await d.dispatch("screenshot", {"fresh": True})
            read_results.append(r["ok"])

    async with anyio.create_task_group() as tg:
        tg.start_soon(reader)
        tg.start_soon(reader)
        res = await d.dispatch("mouse_drag", {
            "from": {"x": 0, "y": 0}, "to": {"x": 500, "y": 500},
            "duration_ms": 200})
        assert res["ok"]

    assert all(read_results) and len(read_results) == 50
    mouse_events = [e[1] for e in driver.pop_events()
                    if e[0] == "mouse"]
    # containment invariant: down first, up last, moves between
    assert mouse_events[0].kind in ("move", "down")
    assert mouse_events[-1].kind == "up"
    kinds = [e.kind for e in mouse_events]
    assert kinds.count("down") == 1 and kinds.count("up") == 1
```

(Adapt the `mouse_drag` args to M1's actual `DragArgs` schema — M1 Task 14 defined it with `from`/`to`/`path`; use whichever field names that model declared, e.g. `from_` aliasing.)

- [ ] **Step 2: Run and verify**

Run: `uv run pytest tests/stress -m stress -q`
Expected: 2 passed (≈10–30 s).
Run: `uv run pytest -q` — expected: stress excluded, suite green.

---

## Plan completion criteria

- `uv run pytest -q` green on any OS; `uv run pytest -m perf` and `-m stress` green when invoked explicitly.
- On macOS: `HANDS_E2E_MACOS=1 uv run pytest tests/e2e -q` green — the agent loop (wait → find_text → click → verify → type → verify) works against a real app.
- A demo plugin package exposing `[project.entry-points."hands.plugins"]` loads at startup, registers a tool visible over MCP, and is refused when not on a configured allowlist; a plugin that raises in `setup` is skipped with the server unaffected.
- `execute_sequence` runs a click→type macro in one MCP round trip, halts on unmet guards with evidence, and rejects read tools, nesting, and any step the policy denies — before executing anything.
- 22 tools registered.
- Nothing committed to git (user instruction).
