"""All coordinate conversions live here and nowhere else (DESIGN §4.12)."""
from __future__ import annotations

from dataclasses import asdict

from ..errors import InvalidArgsError
from ..types import DisplayInfo, Point, Region


class CoordinateMapper:
    def __init__(self, displays: list[DisplayInfo]) -> None:
        if not displays:
            raise InvalidArgsError("no displays reported by driver")
        self._displays = displays
        self._main = next(d for d in displays if d.is_main)

    def display_for(self, p: Point) -> DisplayInfo:
        for d in self._displays:
            if d.bounds_pt.contains(p):
                return d
        raise InvalidArgsError(
            f"point ({p.x}, {p.y}) is outside all displays",
            details={"main_bounds": asdict(self._main.bounds_pt)},
            remediation="take a screenshot and recompute, or pass clamp=true")

    def clamp(self, p: Point) -> Point:
        b = self._main.bounds_pt
        x = min(max(p.x, b.x), b.x + b.width - 1)
        y = min(max(p.y, b.y), b.y + b.height - 1)
        return Point(x, y)

    def screenshot_px_to_pt(self, px: Point, *, bounds_pt: Region,
                            px_per_pt: float) -> Point:
        return Point(bounds_pt.x + px.x / px_per_pt,
                     bounds_pt.y + px.y / px_per_pt)

    def vision_normalized_to_pt(self, nx: float, ny: float, nw: float,
                                nh: float, frame_bounds: Region) -> Region:
        """Vision.framework boxes are normalized with a BOTTOM-LEFT origin;
        flip y and scale into canonical points (DESIGN §4.12)."""
        return Region(
            frame_bounds.x + nx * frame_bounds.width,
            frame_bounds.y + (1.0 - ny - nh) * frame_bounds.height,
            nw * frame_bounds.width,
            nh * frame_bounds.height,
        )
