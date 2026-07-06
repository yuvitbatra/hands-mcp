# Hands Plugin System

Plugins are Python packages discovered via the `hands.plugins` entry-point group. They register additional MCP tools by calling `ctx.registry.register(ToolSpec(...))` during setup.

## Writing a plugin

```python
# my_plugin/__init__.py
from hands.plugins.api import HandsPlugin, PluginContext
from hands.registry import ToolSpec
from hands.retry import RetryPolicy
from pydantic import BaseModel


class MyPlugin:
    name = "my-plugin"
    version = "1.0.0"

    def setup(self, ctx: PluginContext) -> None:
        class Args(BaseModel, extra="forbid"):
            message: str

        async def hello(args: Args, _ctx) -> dict:
            return {"greeting": f"Hello, {args.message}!"}

        ctx.registry.register(ToolSpec(
            "my_hello", "Say hello.", Args, hello,
            "read", RetryPolicy.read(), idempotent=True))

        # Access services:
        from hands.services.screenshot import ScreenshotService
        screenshot_svc = ctx.service(ScreenshotService)

    def teardown(self) -> None:
        pass  # cleanup, release resources
```

## Registering the entry point

In your `pyproject.toml`:

```toml
[project.entry-points."hands.plugins"]
my-plugin = "my_plugin:MyPlugin"
```

## Available services via `ctx.service()`

| Protocol class | What it provides |
|---|---|
| `Driver` | Low-level HID/screen access |
| `StateManager` | Action history, screenshot metadata |
| `ScreenshotService` | Capture and cache screenshots |
| `OCRService` | Apple Vision text recognition |
| `MouseService` | Move, click, drag, scroll |
| `KeyboardService` | Type text, press keys |
| `ClipboardService` | Read/write clipboard |
| `WindowService` | List, focus, resize windows |
| `AppService` | Launch, close, list apps |
| `Waiter` | Poll until a condition is met |
| `VerificationEngine` | Confidence-scored outcome checks |

## Allowlist (security)

Set `HANDS_SECURITY__PLUGIN_ALLOWLIST=["my-plugin"]` or via `HandsConfig` to restrict which plugins may load. Any plugin not on the list is skipped (and logged as `plugin_skipped_not_allowlisted`), not loaded silently (DESIGN §13.7).

## Error handling

A plugin that raises in `setup()` is logged and skipped. The server continues to start. A plugin that raises in `teardown()` is also logged and skipped; remaining plugins are still torn down in reverse load order.
