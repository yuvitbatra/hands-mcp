"""Clipboard service (DESIGN §4.7). Restore-after-paste is on by default:
agents must not destroy the user's clipboard."""
from __future__ import annotations

from typing import Literal

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import PolicyDeniedError
from ..types import ClipboardContent, KeyChord
from .keyboard import KeyboardService


class ClipboardService:
    def __init__(self, driver: Driver, keyboard: KeyboardService,
                 config: HandsConfig) -> None:
        self._driver = driver
        self._keyboard = keyboard
        self._cfg = config.clipboard

    async def get(self, fmt: Literal["text", "image", "any"] = "any"
                  ) -> ClipboardContent:
        if await anyio.to_thread.run_sync(self._driver.secure_input_active):
            raise PolicyDeniedError(
                "secure text entry is active (a password field is focused); "
                "clipboard reads are refused (DESIGN §13.5)")
        content = await anyio.to_thread.run_sync(self._driver.clipboard_read)
        if fmt != "any" and content.kind != fmt:
            return ClipboardContent("empty")
        return content

    async def set(self, content: ClipboardContent) -> None:
        await anyio.to_thread.run_sync(self._driver.clipboard_write, content)

    async def paste(self, text: str, restore: bool = True) -> None:
        saved = await anyio.to_thread.run_sync(self._driver.clipboard_read)
        await self.set(ClipboardContent("text", text=text))
        await self._keyboard.press(KeyChord.parse("cmd+v"))
        if restore:
            await anyio.sleep(self._cfg.restore_delay_ms / 1000)
            await self.set(saved)
