"""Session memory: advisory cache, never authority (DESIGN §8)."""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .config import HandsConfig
from .errors import HandsError
from .types import Point


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    text = out.get("text")
    if isinstance(text, str):
        out["text"] = {"len": len(text),
                       "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return out


@dataclass(frozen=True, slots=True)
class ActionRecord:
    request_id: str
    tool: str
    args: dict[str, Any]
    outcome: str
    duration_s: float
    error: dict[str, Any] | None = None
    ts: float = 0.0

    @classmethod
    def ok(cls, request_id: str, tool: str, args: dict[str, Any],
           duration_s: float) -> "ActionRecord":
        return cls(request_id, tool, _redact(args), "ok", duration_s,
                   None, time.monotonic())

    @classmethod
    def failed(cls, request_id: str, tool: str, args: dict[str, Any],
               err: HandsError) -> "ActionRecord":
        return cls(request_id, tool, _redact(args), err.code, 0.0,
                   err.to_wire(), time.monotonic())


class StateManager:
    def __init__(self, config: HandsConfig) -> None:
        self._history: deque[ActionRecord] = deque(
            maxlen=config.state.history_len)
        self._screen_dirty = True   # nothing observed yet
        self.cursor: Point | None = None
        self.latest_screenshot_meta: dict[str, Any] | None = None

    def record_action(self, rec: ActionRecord) -> None:
        self._history.append(rec)

    def history(self, n: int) -> list[ActionRecord]:
        return list(self._history)[-n:]

    @property
    def screen_dirty(self) -> bool:
        return self._screen_dirty

    def mark_screen_dirty(self) -> None:
        self._screen_dirty = True

    def clear_screen_dirty(self) -> None:
        self._screen_dirty = False
