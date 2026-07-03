"""Shared value objects. All coordinates are logical points, top-left origin
of the main display, y-down (DESIGN §4.12)."""
from __future__ import annotations

import difflib
import enum
from dataclasses import dataclass
from typing import Literal

from .errors import InvalidArgsError


class MouseButton(enum.StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ModifierFlags(enum.Flag):
    NONE = 0
    CMD = enum.auto()
    SHIFT = enum.auto()
    ALT = enum.auto()
    CTRL = enum.auto()


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def offset(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True, slots=True)
class Region:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, p: Point) -> bool:
        return (self.x <= p.x < self.x + self.width
                and self.y <= p.y < self.y + self.height)


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    display_id: int
    bounds_pt: Region
    scale: float          # physical px per logical pt (2.0 on Retina)
    is_main: bool


# macOS virtual key codes at ANSI positions. Chords only — text typing uses
# layout-independent unicode injection, never these (DESIGN §4.6).
_LETTER_DIGIT_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

KEY_CODES: dict[str, int] = {
    "Return": 36, "Tab": 48, "Space": 49, "Delete": 51, "Escape": 53,
    "Left": 123, "Right": 124, "Down": 125, "Up": 126,
    "Home": 115, "End": 119, "PageUp": 116, "PageDown": 121,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
    "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
    **_LETTER_DIGIT_CODES,
}

MODIFIER_NAMES: dict[str, ModifierFlags] = {
    "cmd": ModifierFlags.CMD, "command": ModifierFlags.CMD,
    "shift": ModifierFlags.SHIFT,
    "alt": ModifierFlags.ALT, "option": ModifierFlags.ALT,
    "ctrl": ModifierFlags.CTRL, "control": ModifierFlags.CTRL,
}

# Virtual key codes for the modifier keys themselves (left-side variants).
MODIFIER_KEYCODES: dict[ModifierFlags, int] = {
    ModifierFlags.CMD: 55,
    ModifierFlags.SHIFT: 56,
    ModifierFlags.ALT: 58,
    ModifierFlags.CTRL: 59,
}


@dataclass(frozen=True, slots=True)
class KeyChord:
    modifiers: ModifierFlags
    key: str
    keycode: int

    @classmethod
    def parse(cls, spec: str) -> "KeyChord":
        parts = [p for p in spec.split("+") if p]
        if not parts:
            raise InvalidArgsError(f"empty key chord: {spec!r}")
        *mod_parts, key_part = parts
        mods = ModifierFlags.NONE
        for m in mod_parts:
            flag = MODIFIER_NAMES.get(m.lower())
            if flag is None:
                raise InvalidArgsError(
                    f"unknown modifier {m!r} in chord {spec!r}",
                    details={"known": sorted(MODIFIER_NAMES)})
            mods |= flag
        key = key_part if key_part in KEY_CODES else key_part.lower()
        if key not in KEY_CODES:
            close = difflib.get_close_matches(key_part, KEY_CODES, n=3)
            raise InvalidArgsError(
                f"unknown key {key_part!r} in chord {spec!r}",
                details={"did_you_mean": close})
        return cls(modifiers=mods, key=key, keycode=KEY_CODES[key])


@dataclass(frozen=True, slots=True)
class TextBox:
    """One OCR result in canonical point coordinates (DESIGN §4.10)."""
    text: str
    region: Region
    confidence: float          # 0..1


@dataclass(frozen=True, slots=True)
class ClipboardContent:
    """Clipboard payload. Sensitive by policy (DESIGN §4.7): never logged,
    never stored in state — only hashes/lengths may leave this object."""
    kind: Literal["text", "image", "empty"]
    text: str | None = None
    image_png: bytes | None = None


@dataclass(frozen=True, slots=True)
class WindowInfo:
    window_ref: str            # opaque "{pid}:{window_number}" (DESIGN §4.8)
    app_name: str
    bundle_id: str | None
    pid: int
    title: str
    bounds: Region
    focused: bool
    minimized: bool


@dataclass(frozen=True, slots=True)
class AppInfo:
    bundle_id: str | None
    name: str
    pid: int
    frontmost: bool
