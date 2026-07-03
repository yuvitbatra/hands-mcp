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
