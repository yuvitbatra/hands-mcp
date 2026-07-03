"""Mouse primitives: move/click/drag/scroll (DESIGN §4.5)."""
from __future__ import annotations

from dataclasses import dataclass

import anyio

from ..config import HandsConfig
from ..driver.base import Driver, MouseEventSpec
from ..errors import HandsError
from ..state import StateManager
from ..types import ModifierFlags, MouseButton, Point
from .coords import CoordinateMapper


@dataclass(frozen=True, slots=True)
class ClickResult:
    cursor: Point


class MouseService:
    def __init__(self, driver: Driver, coords: CoordinateMapper,
                 state: StateManager, config: HandsConfig) -> None:
        self._driver = driver
        self._coords = coords
        self._state = state
        self._cfg = config.mouse

    async def move(self, to: Point, duration_ms: int = 0,
                   clamp: bool = False) -> Point:
        to = self._resolve(to, clamp)
        steps = max(1, duration_ms // 16)  # ~60 Hz interpolation
        start = self._state.cursor or self._driver.cursor_position()
        posted = 0
        try:
            for i in range(1, steps + 1):
                t = i / steps
                p = Point(start.x + (to.x - start.x) * t,
                          start.y + (to.y - start.y) * t)
                await self._post(MouseEventSpec("move", p, MouseButton.LEFT))
                posted += 1
                if steps > 1:
                    await anyio.sleep(duration_ms / steps / 1000)
        except HandsError as err:
            if posted:
                err.details["side_effect"] = True
            raise
        self._state.cursor = to
        return to

    async def click(self, at: Point | None,
                    button: MouseButton = MouseButton.LEFT, count: int = 1,
                    modifiers: ModifierFlags = ModifierFlags.NONE,
                    clamp: bool = False) -> ClickResult:
        pos = (await self._move_for_click(at, button, modifiers, clamp)
               if at is not None
               else self._driver.cursor_position())
        delay = self._cfg.click_delay_ms / 1000
        posted = 0
        try:
            for n in range(1, count + 1):
                await self._post(MouseEventSpec("down", pos, button,
                                                click_count=n,
                                                modifiers=modifiers))
                posted += 1
                await anyio.sleep(delay)
                await self._post(MouseEventSpec("up", pos, button,
                                                click_count=n,
                                                modifiers=modifiers))
                posted += 1
                await anyio.sleep(delay)
        except HandsError as err:
            if posted:
                err.details["side_effect"] = True
            raise
        self._state.cursor = pos
        return ClickResult(cursor=pos)

    async def drag(self, path: list[Point], duration_ms: int | None = None,
                   button: MouseButton = MouseButton.LEFT) -> None:
        if len(path) < 2:
            from ..errors import InvalidArgsError
            raise InvalidArgsError("drag path needs at least 2 points")
        pts = [self._resolve(p, clamp=False) for p in path]
        duration = (self._cfg.drag_duration_ms if duration_ms is None
                    else duration_ms)
        await self.move(pts[0])
        await self._post(MouseEventSpec("down", pts[0], button))
        end = pts[0]
        try:
            steps = max(self._cfg.drag_steps, len(pts) - 1)
            waypoints = _interpolate(pts, steps)
            for p in waypoints:
                await self._post(MouseEventSpec("move", p, button))
                end = p
                if duration:
                    await anyio.sleep(duration / steps / 1000)
        except HandsError as err:
            err.details["side_effect"] = True
            err.details["released_at"] = {"x": end.x, "y": end.y}
            raise
        finally:
            # Never leave a phantom drag (DESIGN §5.4).
            self._driver.post_mouse(MouseEventSpec("up", end, button))
            self._state.cursor = end

    async def scroll(self, at: Point | None, dx: int, dy: int,
                     pixels: bool = False) -> None:
        if at is not None:
            await self.move(at)
        pos = at or self._driver.cursor_position()
        try:
            await anyio.to_thread.run_sync(
                self._driver.post_scroll, pos, dx, dy, pixels)
        except HandsError as err:
            if at is not None:
                err.details["side_effect"] = True  # we already moved
            raise

    async def _move_for_click(self, at: Point, button: MouseButton,
                              modifiers: ModifierFlags,
                              clamp: bool) -> Point:
        pos = self._resolve(at, clamp)
        await self._post(MouseEventSpec("move", pos, button,
                                        modifiers=modifiers))
        self._state.cursor = pos
        return pos

    def _resolve(self, p: Point, clamp: bool) -> Point:
        if clamp:
            return self._coords.clamp(p)
        self._coords.display_for(p)   # raises InvalidArgsError if outside
        return p

    async def _post(self, ev: MouseEventSpec) -> None:
        await anyio.to_thread.run_sync(self._driver.post_mouse, ev)


def _interpolate(pts: list[Point], steps: int) -> list[Point]:
    """Evenly interpolate `steps` waypoints along the polyline `pts`."""
    out: list[Point] = []
    segs = len(pts) - 1
    per_seg = max(1, steps // segs)
    for i in range(segs):
        a, b = pts[i], pts[i + 1]
        for j in range(1, per_seg + 1):
            t = j / per_seg
            out.append(Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))
    if out and out[-1] != pts[-1]:
        out.append(pts[-1])
    return out
