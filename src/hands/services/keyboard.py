"""Keyboard: layout-safe unicode typing + real-keycode chords (DESIGN §4.6)."""
from __future__ import annotations

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import HandsError, PolicyDeniedError
from ..types import KeyChord, MODIFIER_KEYCODES, ModifierFlags


class KeyboardService:
    def __init__(self, driver: Driver, config: HandsConfig) -> None:
        self._driver = driver
        self._cfg = config.keyboard
        self._held: list[ModifierFlags] = []   # invariant: mirrors reality

    async def type_text(self, text: str,
                        chunk_delay_ms: int | None = None) -> int:
        if await anyio.to_thread.run_sync(
                self._driver.secure_input_active):
            raise PolicyDeniedError(
                "secure text entry is active (a password field is "
                "focused); typing is refused (DESIGN §13.5)")
        delay = (self._cfg.chunk_delay_ms if chunk_delay_ms is None
                 else chunk_delay_ms) / 1000
        typed = 0
        for chunk in _chunks(text, self._cfg.chunk_size):
            try:
                await anyio.to_thread.run_sync(
                    self._driver.type_unicode, chunk)
            except HandsError as err:
                err.details["chars_typed"] = typed
                err.details["side_effect"] = typed > 0
                raise
            typed += len(chunk)
            if delay:
                await anyio.sleep(delay)
        return typed

    async def press(self, chord: KeyChord, repeat: int = 1) -> None:
        try:
            self._hold(chord.modifiers)
            for _ in range(repeat):
                await anyio.to_thread.run_sync(
                    self._driver.post_key, chord.keycode, True,
                    chord.modifiers)
                await anyio.to_thread.run_sync(
                    self._driver.post_key, chord.keycode, False,
                    chord.modifiers)
        finally:
            self.release_all()   # never leave a modifier held (DESIGN §4.6)

    def release_all(self) -> None:
        """Synchronous so shutdown paths can call it (DESIGN §2.6)."""
        while self._held:
            flag = self._held.pop()
            try:
                self._driver.post_key(MODIFIER_KEYCODES[flag], False, flag)
            except Exception:  # noqa: BLE001 — best-effort during teardown
                pass

    def _hold(self, mods: ModifierFlags) -> None:
        for flag in (ModifierFlags.CMD, ModifierFlags.SHIFT,
                     ModifierFlags.ALT, ModifierFlags.CTRL):
            if flag in mods:
                self._driver.post_key(MODIFIER_KEYCODES[flag], True, flag)
                self._held.append(flag)


def _chunks(s: str, n: int):
    for i in range(0, len(s), n):
        yield s[i:i + n]
