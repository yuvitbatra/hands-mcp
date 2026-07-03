"""Keyboard tools."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import KeyChord


class TypeArgs(BaseModel, extra="forbid"):
    text: str = Field(min_length=1, max_length=10_000)
    chunk_delay_ms: int | None = Field(default=None, ge=0, le=1000)


class PressArgs(BaseModel, extra="forbid"):
    chord: str = Field(min_length=1, max_length=64)
    repeat: int = Field(default=1, ge=1, le=50)


def register(registry: ToolRegistry, container) -> None:
    keyboard = container.keyboard

    async def type_text(args: TypeArgs, ctx) -> dict:
        n = await keyboard.type_text(args.text, args.chunk_delay_ms)
        return {"chars_typed": n, "screen_dirty": True}

    async def press(args: PressArgs, ctx) -> dict:
        chord = KeyChord.parse(args.chord)   # raises InvalidArgsError
        await keyboard.press(chord, args.repeat)
        return {"screen_dirty": True}

    registry.register(ToolSpec(
        "keyboard_type",
        "Type text into the focused element using layout-independent "
        "unicode injection. Click the target field first.",
        TypeArgs, type_text, "act", RetryPolicy.pre_side_effect()))
    registry.register(ToolSpec(
        "key_press",
        "Press a key or shortcut chord, e.g. 'Return', 'cmd+s', "
        "'cmd+shift+p', 'F5'. Use keyboard_type for regular text.",
        PressArgs, press, "act", RetryPolicy.pre_side_effect()))
