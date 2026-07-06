# Tool Reference

All 22 tools registered by `hands serve`, grouped by area. Policy classes:

- **read** — always allowed, never rate-limited, never mutates anything.
- **act** — allowed by default; rate-limited (10/s by default); some escalate
  to **sensitive** under specific arguments (noted below).
- **sensitive** — requires confirmation under the `default` security profile
  (see [README.md § Security Model](../README.md#security-model)).

Every tool response is wrapped in an envelope: `{"ok": true, "request_id": "...", ...fields}`
on success, or `{"ok": false, "request_id": "...", "error": {"code", "message", "retryable", "remediation", "details"}}`
on failure. Fields below are the tool-specific payload merged into that envelope.

Coordinates are always **logical points, top-left origin of the main display, y-down**.

---

## Observation

### `screenshot` — read
Capture the screen, or a region in points.

| Arg | Type | Default | Notes |
|---|---|---|---|
| `region` | `{x, y, width, height}` | full screen | |
| `format` | `"png" \| "jpeg"` | `"png"` | |
| `max_dim` | `int` (64–4096) | config default | downscales the longest side |
| `fresh` | `bool` | `false` | bypass the cache |

Returns `image_b64` plus metadata (`bounds_pt`, `px_per_pt`, cache info). Take
a screenshot before any coordinate-based action.

### `get_state` — read
Re-orientation snapshot: cursor position, displays, last screenshot metadata,
kill-switch status, and (if `include_history` > 0) recent action history.

### `find_text` — read
OCR the screen (or a region) and return bounding boxes matching `text`.

| Arg | Type | Default |
|---|---|---|
| `text` | `str` (1–200 chars) | required |
| `region` | `{x, y, width, height}` | full screen |
| `fuzzy` | `bool` | `true` (case-insensitive substring either direction, or similarity ≥ 0.8) |

Each match includes a `center` you can pass straight to `mouse_click`.

### `get_ui_tree` — read
Accessibility (AX) tree for an app — roles, titles, values, clickable regions
— as ground truth alongside OCR for apps that expose it well.

| Arg | Type | Default |
|---|---|---|
| `app` | `str \| null` | frontmost app if omitted |
| `max_depth` | `int` (1–20) | `8` |

Response includes `truncated: bool` if the tree exceeded `config.ax.max_nodes`
(default 500; nodes are kept in depth-first traversal order and pruned once
the cap is hit).

### `wait` — read
Poll until a condition is met, or a fixed duration elapses. A timeout returns
`met: false` — that's an answer, not an error.

Condition types: `duration` (`{ms}`), `text_present` (`{text, region?}`,
**exact** substring match), `screen_stable` (`{quiet_ms}`), `window_present`
(`{app?, title?}`), `window_gone` (`{app?, title?}` — matches the same way as
`window_present` and reports "gone" when nothing matches; it does not take a
`window_ref`), `app_frontmost` (`{app}`).

### `verify` — read
Check an expected outcome after acting; returns pass/fail plus evidence — the
close-the-loop tool for confirming an action actually worked.

`expect` types: `text_present` / `text_absent` (exact substring),
`region_changed` / `region_unchanged` (needs `baseline_screenshot_id` from an
earlier `screenshot` response), `cursor_at`, `window_present` / `window_gone`,
`clipboard_contains` (evidence never includes clipboard content — only
`matched`/`clipboard_len`), and compound `all_of` / `any_of` with `children`.

---

## Mouse

### `mouse_move` — act
`{x, y, duration_ms?, clamp?}` → moves the cursor, optionally animated.

### `mouse_click` — act
`{x?, y?, button?, count?, modifiers?, clamp?}` → clicks at `(x, y)` or the
current cursor position if omitted. `button`: `left` | `right` | `middle`.
`modifiers`: list of `"cmd"`, `"shift"`, `"alt"`, `"ctrl"`.

### `mouse_drag` — act
`{path: [{x,y}, ...] (2-64 points), duration_ms?, button?}` → press, move
through the path, release.

### `mouse_scroll` — act
`{x?, y?, dx?, dy?, pixels?}` → scroll at a point (or the current cursor).
Positive `dy` scrolls up, negative down; wheel ticks by default, pixels if
`pixels: true`.

---

## Keyboard

### `keyboard_type` — act
`{text (1-10,000 chars), chunk_delay_ms?}` → layout-independent unicode
injection into the focused element. **Refused while secure text entry is
active** (a password field has focus) — see Security Model.

### `key_press` — act
`{chord, repeat?}` → e.g. `"Return"`, `"cmd+s"`, `"cmd+shift+p"`, `"F5"`.

---

## Clipboard

### `clipboard_get` — sensitive
`{format?: "text" | "image" | "any"}` → reads the clipboard. Requires
confirmation under the `default` profile; refused outright while secure text
entry is active.

### `clipboard_set` — act
`{text?, image_b64?}` (exactly one) → sets the clipboard.

### `clipboard_paste` — act
`{text (≤100,000 chars), restore?: bool}` → saves the current clipboard, sets
it to `text`, presses Cmd+V, then restores the original clipboard (unless
`restore: false`). Preferred over `keyboard_type` for long text.

---

## Windows

### `window_list` — read
`{app?, on_screen_only?}` → list windows, optionally filtered by app bundle
id or name (case-insensitive). Each entry includes an opaque `window_ref`.

### `window_focus` — act
`{window_ref?, app?, title_match?}` (at least one) → raises/focuses a window.
Stale `window_ref`s (e.g. after a title changed) are re-resolved by matching
pid + fuzzy title similarity before failing.

### `window_manage` — act (escalates to **sensitive** when `action: "close"`)
`{window_ref, action, bounds?}` → `action` is one of `move`, `resize`,
`minimize`, `unminimize`, `maximize`, `close`. `bounds: {x, y, width, height}`
is required for `move`/`resize`. `close` may trigger "Don't Save" dialogs and
needs confirmation under the default policy.

---

## Apps

### `app_open` — act
`{app, wait_for_window?, timeout_ms?}` → launches an app by bundle id
(preferred) or name, or activates it if already running. `wait_for_window`
(default `true`) waits for its first window to appear.

### `app_close` — act (escalates to **sensitive** when `force: true`)
`{app, force?}` → quits gracefully, or force-terminates when `force: true`
(needs confirmation under the default policy).

### `app_list` — read
`{}` → running apps plus which one is frontmost.

---

## Batching

### `execute_sequence` — act
Run up to 20 pre-decided acting steps in a single MCP round trip — a macro,
not a planner. Every step is validated and authorized **up front**, before
anything executes; any denied step rejects the whole sequence with nothing
run.

| Arg | Type | Default |
|---|---|---|
| `steps` | `[{tool, args?, guard?, guard_timeout_ms?}]` (1–20) | required |
| `stop_on_failure` | `bool` | `true` |
| `screenshot_after` | `bool` | `true` |

Rules: no `read`-class tools (call them directly instead), no nesting
(`execute_sequence` inside `execute_sequence`), and every step is subject to
the *same* app deny-list / confirmation / escalation rules as a direct
`dispatch()` call (the sequence itself is rate-limited as a single call, not
per-step — see [README.md § Security Model](../README.md#security-model)).

A step's optional `guard` uses the same condition schema as `wait`; an unmet
guard halts the sequence (`halted_by: "guard"`, plus `guard_evidence`) rather
than erroring. Response: `{results: [...], completed, halted_by?,
guard_evidence?, final_screenshot?}` — `image_b64` for the final screenshot
is hoisted to the top level of the envelope so MCP clients render it as an
image, not raw base64 text.

---

For the schema types (`Region`, `Point`, `WindowInfo`, `AppInfo`, `AXNode`,
`ClipboardContent`) and the full design rationale behind each tool, see
[docs/DESIGN.md](DESIGN.md).
