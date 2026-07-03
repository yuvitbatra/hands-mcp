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
