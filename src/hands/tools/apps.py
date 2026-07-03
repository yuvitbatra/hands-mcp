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
