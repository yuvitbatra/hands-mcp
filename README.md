<div align="center">

# 🖐️ Hands

**A macOS computer-use MCP server for autonomous AI agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-215%20passing-brightgreen.svg)](#testing)
[![MCP](https://img.shields.io/badge/MCP-compatible-orange.svg)](https://modelcontextprotocol.io)
[![CI](https://github.com/yuvitbatra/hands-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/yuvitbatra/hands-mcp/actions/workflows/ci.yml)

Give an LLM eyes, a mouse, and a keyboard — with a real security model in
between.

[Quick Start](#quick-start) •
[Tools](#tools-22) •
[Security Model](#security-model) •
[Plugins](#plugin-system) •
[Docs](docs/DESIGN.md)

</div>

---

Most "computer use" demos give a model raw pixel coordinates and hope for the
best. Hands is built differently: every action goes through a typed
7-phase dispatch pipeline — validate → rate-limit → authorize → lock →
execute-with-retry → observe → audit — so that giving an agent control of
your desktop doesn't mean giving up any of your say in what it's allowed to
do.

```python
import anyio
from hands import Container, HandsConfig

config = HandsConfig()
config.driver = "fake"          # no macOS needed — great for a first look
container = Container.build(config)

async def main():
    shot = await container.dispatcher.dispatch("screenshot", {})
    print(shot["ok"], shot["bounds_pt"])

anyio.run(main)
```

## Why Hands

- **A real policy engine, not a rubber stamp.** Rule-based profiles
  (`strict` / `default` / `trusted`), an app deny-list (Passwords,
  1Password, System Settings — blocked out of the box), secret-pattern
  detection on typed text, per-tool confirmation, and a sliding-window rate
  limiter. See [Security Model](#security-model).
- **Tamper-evident audit log.** Every action is appended to a SHA-256
  hash-chained JSONL log; deleting or editing any line downstream breaks the
  chain, and `hands audit verify` proves it.
- **Redaction by construction.** Clipboard content and typed text never
  enter application state, logs, audit records, or metrics — only their
  length and a SHA-256 hash ever do.
- **A fake driver, not just mocks.** `FakeDriver` is a full in-memory virtual
  desktop (windows, apps, clipboard, AX tree, OCR boxes) that every tool runs
  against identically to the real macOS driver. The entire test suite — 215
  tests — runs on Linux CI with zero macOS hardware, and it's the fastest way
  to try Hands out or build a plugin without touching your real screen.
- **Perception, not just action.** OCR-grounded text search (`find_text`),
  an accessibility-tree fallback (`get_ui_tree`), and a `verify`/`wait`
  condition language so an agent can confirm an action worked instead of
  flying blind.
- **A kill switch that actually works.** `touch ~/.hands/KILL` halts the
  server immediately — checked before every single dispatch, including
  mid-way through a batched `execute_sequence`.
- **Extensible without forking.** Third-party plugins register tools through
  the exact same `ToolSpec` machinery as the 22 built-ins, loaded via Python
  entry points; a plugin that raises during setup is logged and skipped, not
  a server crash. (Plugins run in-process with access to the same services —
  this is crash isolation, not a security sandbox; don't load untrusted
  plugin code any more than you would an untrusted import.)

## Requirements

- macOS 13+ (Ventura or later) for real desktop control
- Python 3.12+
- Screen Recording and Accessibility permissions (System Settings → Privacy
  & Security) — `hands permissions` tells you exactly what's missing

You can install and explore the library on any OS using the fake driver
(`HANDS_DRIVER=fake`) — only real desktop control requires macOS.

## Installation

```bash
pip install hands-mcp
```

With the macOS driver and Vision OCR (needed for real desktop control):

```bash
pip install "hands-mcp[macos]"
```

The import name is still `hands` regardless of the install name above:

```python
import hands
print(hands.__version__)
```

For development:

```bash
git clone https://github.com/yuvitbatra/hands-mcp.git
cd hands-mcp
uv sync --group dev
uv run pytest -q
```

## Quick Start

### As an MCP server (for AI agents)

Add to your MCP client configuration (e.g. Claude Desktop's
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hands": {
      "command": "hands",
      "args": ["serve"]
    }
  }
}
```

Or run it directly over stdio:

```bash
hands serve
```

### As a Python library

```python
import anyio
from hands import Container, HandsConfig

# The fake driver is a full in-memory virtual desktop — no macOS needed.
config = HandsConfig()
config.driver = "fake"
container = Container.build(config)

async def main():
    res = await container.dispatcher.dispatch("screenshot", {})
    print(res["ok"])  # True

anyio.run(main)
```

```python
# Or run the full MCP server over stdio from your own process:
import anyio
from hands import run_server, load_config

anyio.run(run_server, load_config())
```

### Diagnostics

```bash
hands doctor              # resolved config, driver, displays, registered tools
hands doctor --metrics    # ...plus a metrics snapshot (counters + latency histograms)
hands permissions         # TCC grant status (Screen Recording, Accessibility) + fix links
hands audit verify        # verify the audit log's hash chain end to end
```

## Tools (22)

Full argument/return reference: [`docs/TOOLS.md`](docs/TOOLS.md).

| Tool | Policy | Description |
|---|---|---|
| `screenshot` | read | Capture the screen (or a region); cached by default |
| `get_state` | read | Session state: cursor, displays, action history, dirty flag |
| `find_text` | read | OCR search returning bounding boxes for matching text |
| `get_ui_tree` | read | Accessibility tree for an app (roles, labels, frames) |
| `wait` | read | Poll until a condition is met: text present, window state, duration |
| `verify` | read | Check an expected outcome after acting; returns evidence |
| `mouse_move` | act | Move the cursor |
| `mouse_click` | act | Click at a point (single, double, or triple) |
| `mouse_drag` | act | Click-drag along a path |
| `mouse_scroll` | act | Scroll at a point |
| `keyboard_type` | act | Type text into the focused element (refused during secure input) |
| `key_press` | act | Press a key or chord (e.g. `cmd+s`, `Return`) |
| `clipboard_get` | **sensitive** | Read the current clipboard (text or image) |
| `clipboard_set` | act | Set the clipboard to text or a PNG image |
| `clipboard_paste` | act | Paste via clipboard + Cmd+V, restoring the clipboard after |
| `window_list` | read | List windows, optionally filtered by app |
| `window_focus` | act | Bring a window to front (stale refs re-resolve by fuzzy title) |
| `window_manage` | act → **sensitive** on `close` | Move, resize, minimize, maximize, or close a window |
| `app_open` | act | Launch an app by bundle ID or name, waiting for its window |
| `app_close` | act → **sensitive** on `force` | Quit an app gracefully, or force-terminate |
| `app_list` | read | List running apps and the frontmost one |
| `execute_sequence` | act | Batch up to 20 pre-decided acting steps in one round trip, gated by guard conditions |

## Security Model

An agent that can move your mouse and type on your behalf is a genuinely
different risk profile from a chatbot, so the policy layer isn't an
afterthought — it's a fixed phase of every dispatch, before the tool ever
runs:

```
validate args → rate limit → authorize (PermissionEngine) → acquire action lock
    → execute with retry → record state/screen-dirty → audit + metrics
```

**Profiles** (`HANDS_SECURITY__PROFILE`, default `default`):

| Profile | `act` tools | `sensitive` tools |
|---|---|---|
| `strict` | requires confirmation | requires confirmation |
| `default` | allowed | requires confirmation |
| `trusted` | allowed | allowed |

**Evaluation order** for every action: (1) is the frontmost app on the
deny-list? → denied outright, even under `trusted`. (2) does a configured
rule match? → first match wins. (3) does typed/pasted text match a
`secret_patterns` regex? → confirmation required regardless of profile. (4)
fall through to the profile's class default above.

**App deny-list** (`HANDS_SECURITY__DENY_APPS`, on by default): blocks
acting tools — reads still work — against `com.apple.systempreferences*`,
`com.apple.Passwords*`, `com.apple.keychainaccess`, 1Password (both bundle
id families). This applies uniformly whether the tool is called directly or
from inside `execute_sequence`.

**Rate limiting** (`HANDS_SECURITY__MAX_ACTIONS_PER_S`, default `10.0`): a
1-second sliding window over acting tools; bursts past the limit are denied,
not queued.

**Secure input refusal**: `keyboard_type` and `clipboard_get` are refused
outright while macOS reports secure text entry active (a password field has
focus) — independent of profile.

**Confirmation hooks**: under `dialog` mode (default, macOS only) a
denied-by-default `sensitive` action pops a real confirmation dialog via
`osascript`; under `deny` mode (or off-macOS) it's auto-denied. Plug in your
own hook by subclassing the `PermissionEngine`'s confirm callback if you need
a different UX (e.g. routing through your agent framework's own approval
flow).

**Redaction invariant**: clipboard content and typed text are used only
in-memory for policy matching (e.g. secret-pattern checks) — they never
reach `state`, the audit log, or metrics in raw form. Only length and a
SHA-256 hash ever leave those boundaries.

**Kill switch**: `touch ~/.hands/KILL` halts the server immediately —
checked at the top of every dispatch, including mid-sequence inside
`execute_sequence`. Remove the file to resume.

**Audit log**: every action is appended to `~/.hands/audit.jsonl` as
`{"event", "prev_hash", "hash"}`, where `hash = sha256(prev_hash +
canonical_json(event))`. Tampering with or deleting any line downstream
breaks the chain — verify with `hands audit verify`.

## Configuration

All settings can be overridden via `HANDS_*` environment variables (nested
fields use `__`, e.g. `HANDS_SECURITY__PROFILE`). Full schema:
[`src/hands/config.py`](src/hands/config.py).

| Variable | Default | Description |
|---|---|---|
| `HANDS_DRIVER` | `auto` | `auto` (macOS on darwin, else fake), `fake`, or `macos` |
| `HANDS_SECURITY__PROFILE` | `default` | `strict`, `default`, or `trusted` |
| `HANDS_SECURITY__DENY_APPS` | see above | JSON list of bundle-id globs blocked for acting tools |
| `HANDS_SECURITY__SECRET_PATTERNS` | `[]` | JSON list of regexes that force confirmation on typed/pasted text |
| `HANDS_SECURITY__MAX_ACTIONS_PER_S` | `10.0` | Sliding-window rate limit for acting tools |
| `HANDS_SECURITY__CONFIRMATION` | `dialog` | `dialog` (real macOS confirm dialog) or `deny` (auto-deny, safe for CI) |
| `HANDS_SECURITY__PLUGIN_ALLOWLIST` | (all allowed) | JSON list of allowed plugin entry-point names |
| `HANDS_SCREENSHOT__MAX_DIM` | `1568` | Max screenshot dimension |
| `HANDS_SCREENSHOT__JPEG_QUALITY` | `80` | JPEG quality (0–100) |
| `HANDS_MOUSE__CLICK_DELAY_MS` | `8` | Delay between mouse down and up |
| `HANDS_KEYBOARD__CHUNK_SIZE` | `32` | Characters per typing chunk |
| `HANDS_AX__MAX_NODES` | `500` | Depth-first-traversal node cap for `get_ui_tree` |

## Plugin System

Hands supports external plugins that register custom tools through the exact
same `ToolSpec` machinery as the built-ins — no side door around the
dispatcher, so plugin tools get the same policy, retry, and audit treatment.

```python
from hands.plugins.api import HandsPlugin, PluginContext

class MyPlugin:
    name = "my-plugin"
    version = "1.0.0"

    def setup(self, ctx: PluginContext) -> None:
        from pydantic import BaseModel
        from hands.registry import ToolSpec
        from hands.retry import RetryPolicy

        class Args(BaseModel, extra="forbid"):
            query: str

        async def my_tool(args: Args, ctx_) -> dict:
            return {"result": f"searched for {args.query}"}

        ctx.registry.register(ToolSpec(
            "my_search", "Custom search tool",
            Args, my_tool, "read", RetryPolicy.read(), idempotent=True))

    def teardown(self) -> None:
        pass
```

Register it via the `hands.plugins` entry-point group in your own package's
`pyproject.toml`:

```toml
[project.entry-points."hands.plugins"]
my-plugin = "my_package.plugin:MyPlugin"
```

Plugins load at server startup. **A plugin that raises in `setup` is logged
and skipped — it never takes the server down.** Restrict which plugins may
load with an allowlist:

```bash
HANDS_SECURITY__PLUGIN_ALLOWLIST='["my-plugin"]' hands serve
```

See [`docs/plugins.md`](docs/plugins.md) for the full plugin author's guide,
and [`src/hands/plugins/api.py`](src/hands/plugins/api.py) for the stable
import surface (semver-guarded — additive-only within a major version).

## Testing

```bash
uv run pytest -q                                          # 215 tests, any OS, ~2s
HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q    # real macOS driver (needs TCC grants)
HANDS_E2E_MACOS=1 uv run pytest tests/e2e -q              # full stack vs. a real Tk fixture app
uv run pytest tests/perf -m perf --benchmark-only -q      # latency budgets
uv run pytest tests/stress -m stress -q                   # 10k-action soak + concurrency
```

The contract/e2e suites are opt-in because they genuinely move your mouse
and open/close real apps — don't run them unattended. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full development workflow.

## Architecture

```
types, errors, config, retry
    ↓
driver/base (Protocol) ← fake.py (tests/CI) + macos.py (real hardware)
    ↓
services/  (screenshot, ocr, mouse, keyboard, clipboard, windows, apps,
            waiter, verification)
    ↓
state, permissions (PermissionEngine), audit (hash-chained), metrics, registry
    ↓
dispatcher  (7-phase pipeline: validate → rate-limit → authorize → lock →
             execute-with-retry → observe → audit)
    ↓
tools/      (22 MCP tools)
    ↓
container → server (MCP stdio transport) → cli
```

Full design rationale: [`docs/DESIGN.md`](docs/DESIGN.md).

## Contributing

Contributions welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
development setup, test-driven workflow, and what a good PR looks like.

## License

[MIT](LICENSE)
