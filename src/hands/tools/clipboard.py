"""MCP clipboard tools (DESIGN §5.8–5.9)."""
from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..types import ClipboardContent


class ClipboardGetArgs(BaseModel, extra="forbid"):
    format: Literal["text", "image", "any"] = "any"


class ClipboardSetArgs(BaseModel, extra="forbid"):
    text: str | None = Field(default=None, max_length=100_000)
    image_b64: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.text is None) == (self.image_b64 is None):
            raise ValueError("provide exactly one of text or image_b64")
        return self


class ClipboardPasteArgs(BaseModel, extra="forbid"):
    text: str = Field(max_length=100_000)
    restore: bool = True


def register(registry: ToolRegistry, container) -> None:
    clip = container.clipboard

    async def clipboard_get(args: ClipboardGetArgs, ctx) -> dict:
        content = await clip.get(args.format)
        out: dict = {"kind": content.kind}
        if content.text is not None:
            out["text"] = content.text
        if content.image_png is not None:
            out["image_b64"] = base64.b64encode(
                content.image_png).decode()
        return out

    async def clipboard_set(args: ClipboardSetArgs, ctx) -> dict:
        if args.text is not None:
            await clip.set(ClipboardContent("text", text=args.text))
        else:
            await clip.set(ClipboardContent(
                "image", image_png=base64.b64decode(args.image_b64)))
        return {}

    async def clipboard_paste(args: ClipboardPasteArgs, ctx) -> dict:
        await clip.paste(args.text, args.restore)
        return {}

    registry.register(ToolSpec(
        "clipboard_get",
        "Read the clipboard (sensitive: may require user confirmation). "
        "Refused while a password field has focus.",
        ClipboardGetArgs, clipboard_get, "sensitive", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "clipboard_set",
        "Set the clipboard to text or a base64 PNG.",
        ClipboardSetArgs, clipboard_set, "act",
        RetryPolicy.pre_side_effect(), idempotent=True))
    registry.register(ToolSpec(
        "clipboard_paste",
        "Paste text into the focused app via clipboard + Cmd+V, then "
        "restore the previous clipboard. Preferred over keyboard_type for "
        "long text.",
        ClipboardPasteArgs, clipboard_paste, "act",
        RetryPolicy.pre_side_effect(), idempotent=False))
