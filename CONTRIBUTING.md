# Contributing to Hands

Thanks for considering a contribution. This project is a macOS computer-use
MCP server, and it takes correctness and security seriously — an agent that
controls your mouse and keyboard has a low margin for silent bugs. This
guide covers how to get set up, how the codebase is organized, and what a
good PR looks like.

## Getting set up

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/YOUR-GITHUB-USERNAME/hands-mcp.git
cd hands-mcp
uv sync --group dev
```

You do **not** need macOS to develop most of this codebase: nearly
everything is written against a `Driver` protocol with two implementations —
`FakeDriver` (an in-memory virtual desktop used by every unit/integration
test, and by CI on both macOS and Linux runners) and `MacOSDriver` (the real
thing, exercised only by opt-in contract/e2e tests on real hardware). If
you're adding a feature, you almost always start with the fake driver.

## Running the test suite

```bash
uv run pytest -q                                    # default: unit + integration
HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q   # real macOS driver, needs TCC grants
HANDS_E2E_MACOS=1 uv run pytest tests/e2e -q             # full stack against a real Tk app
uv run pytest tests/perf -m perf --benchmark-only -q     # latency budgets
uv run pytest tests/stress -m stress -q                  # soak + concurrency
```

The contract, e2e, perf, and stress suites are all opt-in on purpose:

- **contract/e2e** require real Screen Recording + Accessibility permissions
  and will move your actual mouse and open/close real apps — don't run them
  while you're using the machine, and don't run them in a subagent/automation
  context without a human's explicit go-ahead.
- **perf/stress** are excluded from the default run via `pyproject.toml`'s
  `addopts` so they don't add noise/flakiness to a normal `pytest -q`.

## Test-driven development

This codebase was built test-first and expects contributions to follow the
same discipline: write the failing test, watch it fail for the right reason,
implement, watch it pass. A PR that adds behavior without a test that would
have failed before the change will get bounced back.

## Architecture at a glance

```
types, errors, config, retry
    ↓
driver/base (Protocol) ← fake.py (tests/CI) + macos.py (real hardware)
    ↓
services/  (screenshot, ocr, mouse, keyboard, clipboard, windows, apps,
            waiter, verification — one class per capability, thin wrappers
            around the driver)
    ↓
state, permissions (PermissionEngine), audit (hash-chained), metrics, registry
    ↓
dispatcher  (the 7-phase pipeline every tool call goes through)
    ↓
tools/      (22 MCP tools — thin: validate → one service call → shape result)
    ↓
container → server (MCP stdio transport) → cli
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design rationale and
[`docs/TOOLS.md`](docs/TOOLS.md) for the tool reference. If you're adding a
new tool, follow the existing pattern in `src/hands/tools/`: a Pydantic args
model with `extra="forbid"`, a thin async handler, and a `ToolSpec`
registration with the right `policy_class` (`read` / `act` / `sensitive`) and
`RetryPolicy`.

## Adding a new tool or service

1. Add/extend the `Driver` protocol method in `src/hands/driver/base.py` if
   you need new OS-level capability.
2. Implement it in `FakeDriver` first (`src/hands/driver/fake.py`) — this is
   what your tests run against.
3. Add a `services/*.py` class if the capability needs any logic beyond a
   direct pass-through (validation, redaction, retries, stale-ref recovery).
4. Add the tool in `src/hands/tools/*.py`, wire it into `Container` and
   `tools/__init__.py`.
5. Implement the real `MacOSDriver` method last (`src/hands/driver/macos.py`)
   and add a gated contract test in `tests/contract/`.

## Security-sensitive changes

Changes to `src/hands/permissions.py`, `src/hands/dispatcher.py`'s policy
phase, `src/hands/audit.py`, or anything touching clipboard/typed-text
redaction get extra scrutiny. Two invariants that must never regress:

- **Redaction**: clipboard content and typed text never enter state, audit
  logs, or metrics — only length/hash may leave those objects.
- **Fail-closed**: if a permission decision can't be made confidently, the
  action should be denied, not allowed.

## Code style

- No comments explaining *what* code does — names should carry that. A
  comment is for a non-obvious *why* (a workaround, an invariant, a subtle
  constraint).
- Don't add abstractions, config flags, or error handling for scenarios that
  can't happen given this codebase's actual call sites.
- Match the surrounding file's style before introducing a new pattern.

## Pull requests

- Keep PRs scoped to one change. A bug fix doesn't need a drive-by refactor.
- Include the test output in your PR description (`uv run pytest -q`
  summary line at minimum).
- If you're touching the policy engine, plugin loader, or audit log, say so
  explicitly in the PR description — these get a closer look.

## Plugin authors

If you're building a third-party plugin rather than contributing to this
repo directly, you don't need any of the above — see
[`docs/plugins.md`](docs/plugins.md) for the stable plugin API
(`hands.plugins.api`), which is what you should import against.
