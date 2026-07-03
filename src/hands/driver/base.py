"""The OS seam. Dumb by design: no policy, retries, or coordinate math
(DESIGN §6.1). M1 exposes the perception + input subset."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from PIL import Image

from ..types import (ClipboardContent, DisplayInfo, ModifierFlags,
                    MouseButton, Point, Region, WindowInfo)


@dataclass(frozen=True, slots=True, eq=False)
class RawFrame:
    image: Image.Image           # physical pixels
    bounds_pt: Region            # what part of point-space this shows
    px_per_pt: float
    display_id: int


@dataclass(frozen=True, slots=True)
class MouseEventSpec:
    kind: Literal["move", "down", "up"]
    at: Point
    button: MouseButton
    click_count: int = 1
    modifiers: ModifierFlags = ModifierFlags.NONE


@dataclass(frozen=True, slots=True)
class RawTextBox:
    """OCR box exactly as Vision reports it: normalized [0,1], BOTTOM-LEFT
    origin, relative to the recognized frame. Services convert to canonical
    points via CoordinateMapper.vision_normalized_to_pt (DESIGN §4.10)."""
    text: str
    nx: float
    ny: float
    nw: float
    nh: float
    confidence: float


@runtime_checkable
class Driver(Protocol):
    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame: ...
    def displays(self) -> list[DisplayInfo]: ...
    def cursor_position(self) -> Point: ...
    def post_mouse(self, event: MouseEventSpec) -> None: ...
    def post_scroll(self, at: Point, dx: int, dy: int,
                    pixels: bool) -> None: ...
    def type_unicode(self, text: str) -> None: ...
    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None: ...
    def ocr(self, frame: RawFrame,
            languages: list[str]) -> list[RawTextBox]: ...
    def clipboard_read(self) -> ClipboardContent: ...
    def clipboard_write(self, content: ClipboardContent) -> None: ...
    def secure_input_active(self) -> bool: ...
    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]: ...
    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None: ...
