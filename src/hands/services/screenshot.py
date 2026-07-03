"""Capture, scale, encode, cache (DESIGN §4.4)."""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import TargetNotFoundError
from ..state import StateManager
from ..types import Region
from .vision import downscale, encode, perceptual_hash


@dataclass(frozen=True, slots=True)
class Screenshot:
    screenshot_id: str
    data: bytes
    fmt: str
    ts: float
    bounds_pt: Region
    px_per_pt: float
    display_id: int
    phash: str
    cached: bool = False

    def meta(self) -> dict[str, Any]:
        return {
            "screenshot_id": self.screenshot_id,
            "fmt": self.fmt,
            "ts": self.ts,
            "bounds_pt": {"x": self.bounds_pt.x, "y": self.bounds_pt.y,
                          "width": self.bounds_pt.width,
                          "height": self.bounds_pt.height},
            "px_per_pt": self.px_per_pt,
            "display_id": self.display_id,
            "phash": self.phash,
            "cached": self.cached,
        }


class ScreenshotService:
    def __init__(self, driver: Driver, state: StateManager,
                 config: HandsConfig) -> None:
        self._driver = driver
        self._state = state
        self._cfg = config.screenshot
        self._store: OrderedDict[str, Screenshot] = OrderedDict()
        self._max_store = config.state.max_screenshots
        self._last_full: Screenshot | None = None

    async def capture(self, region: Region | None = None,
                      display_id: int | None = None, fmt: str = "png",
                      max_dim: int | None = None,
                      fresh: bool = False) -> Screenshot:
        if region is None and not fresh and self._cache_valid():
            return replace(self._last_full, cached=True)

        raw = await anyio.to_thread.run_sync(
            self._driver.capture, region, display_id)
        img, px_per_pt = downscale(raw, max_dim or self._cfg.max_dim)
        data = encode(img, fmt, self._cfg.jpeg_quality)
        shot = Screenshot(
            screenshot_id=uuid.uuid4().hex[:12], data=data, fmt=fmt,
            ts=time.monotonic(), bounds_pt=raw.bounds_pt,
            px_per_pt=px_per_pt, display_id=raw.display_id,
            phash=perceptual_hash(img))
        self._remember(shot, full=region is None)
        return shot

    def get(self, screenshot_id: str) -> Screenshot:
        try:
            return self._store[screenshot_id]
        except KeyError:
            raise TargetNotFoundError(
                f"screenshot {screenshot_id!r} not found (evicted?)",
                remediation="take a new screenshot") from None

    def _cache_valid(self) -> bool:
        last = self._last_full
        return (last is not None
                and not self._state.screen_dirty
                and time.monotonic() - last.ts < self._cfg.cache_ttl_s)

    def _remember(self, shot: Screenshot, *, full: bool) -> None:
        self._store[shot.screenshot_id] = shot
        while len(self._store) > self._max_store:
            self._store.popitem(last=False)
        if full:
            self._last_full = shot
        self._state.clear_screen_dirty()
        self._state.latest_screenshot_meta = shot.meta()
