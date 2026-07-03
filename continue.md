# Hands — Session Continuation Handoff

> **Last updated:** 2026-07-03  
> **Purpose:** Pick up after M1 without re-reading 3,500 lines of plan.

---

## One-line summary

**M1 complete.** Runnable `hands` MCP server over stdio with 9 tools, fake driver (CI), and macOS driver. **93 tests passing** (`uv run pytest -q`).

---

## What exists right now

| Asset | Status |
|---|---|
| `docs/DESIGN.md` | Complete draft v1.0 |
| `docs/superpowers/plans/2026-07-03-hands-m1-core.md` | All 16 tasks implemented |
| `src/hands/` | Full package: errors, types, config, retry, state, registry, dispatcher, permissions (stub), audit (stub), metrics (stub), driver/{base,fake,macos}, services/*, tools/*, container, server, cli |
| `tests/` | unit + integration + contract (**93 passing**) |
| Git | No commits yet (user instruction) |

### M1 tools (9)

`screenshot`, `get_state`, `wait`, `mouse_move`, `mouse_click`, `mouse_drag`, `mouse_scroll`, `keyboard_type`, `key_press`

---

## Task completion (M1)

| # | Task | Status |
|---|---|---|
| 1–16 | All M1 tasks | ✅ Done |

---

## Verification commands

```bash
cd /Users/yuvitbatra/Desktop/School/summer/mcp/hands

# Full suite (any OS)
uv run pytest -q                    # → 93 passed

# macOS contract tests (needs Screen Recording TCC grant)
HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q

# CLI
HANDS_DRIVER=fake uv run hands doctor
HANDS_DRIVER=fake uv run hands serve   # stdio MCP server

# Manual EOF exit test
printf '' | HANDS_DRIVER=fake uv run hands serve; echo "exit=$?"
```

---

## Known gaps / next steps

1. **macOS contract capture tests** fail without Screen Recording permission — grant it in System Settings → Privacy & Security → Screen Recording for Terminal/Cursor, then re-run `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q`.
2. **M2** (next milestone): OCR (Apple Vision), verification engine, full `wait` conditions, ScreenCaptureKit capture.
3. **No git commits** until user lifts that instruction.
4. Minor: Pillow `getdata()` deprecation warning in `vision.py` — cosmetic, fix when convenient.

---

## Architecture (built)

```
types, errors, config, retry
    ↓
driver/base ← fake (tests) + macos (real)
    ↓
services: vision → coords → screenshot, mouse, keyboard
    ↓
state, AllowAllPermissions, AuditLogger, Metrics, registry, dispatcher
    ↓
tools/ (9 MCP tools)
    ↓
container → server (low-level MCP Server) → cli
```

---

## Constraints (unchanged)

- Python ≥ 3.12, uv, `src/` layout
- Coordinates: logical points, top-left origin, y-down
- `stdout` = MCP only; logging to stderr
- Low-level `mcp.server.Server` (NOT FastMCP)
- No commits unless user asks

---

## Session log

| Date | Progress |
|---|---|
| 2026-07-03 | Tasks 1–6. 28 tests. |
| 2026-07-03 | Tasks 7–16. **M1 complete. 93 tests.** `hands doctor` + e2e MCP verified. |

**Next action:** M2 plan (OCR, verification, full waiter) OR grant TCC and run macOS contract suite.
