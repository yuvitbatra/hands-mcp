"""Poll-based wait-for-condition engine (DESIGN §4.14)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import anyio

from ..config import HandsConfig
from ..errors import InvalidArgsError
from .ocr import OCRService, find_matching_boxes, matches_to_evidence
from .screenshot import ScreenshotService


@dataclass(frozen=True, slots=True)
class WaitResult:
    met: bool
    waited_ms: int
    evidence: dict


class Waiter:
    def __init__(self, screenshots: ScreenshotService, ocr: OCRService,
                 config: HandsConfig, driver=None) -> None:
        self._shots = screenshots
        self._ocr = ocr
        self._cfg = config.waiter
        self._driver = driver
        self._checkers = {
            "text_present": self._text_present,
            "screen_stable": self._screen_stable,
            "window_present": self._window_present,
            "window_gone": self._window_gone,
            "app_frontmost": self._app_frontmost,
        }

    async def wait_for(self, cond: dict, timeout_ms: int) -> WaitResult:
        ctype = cond.get("type")
        if ctype == "duration":
            ms = int(cond.get("ms", 0))
            await anyio.sleep(ms / 1000)
            return WaitResult(True, ms, {})
        checker = self._checkers.get(ctype)
        if checker is None:
            raise InvalidArgsError(
                f"unknown condition type: {ctype!r}",
                details={"known": ["duration", *sorted(self._checkers)]})
        start = time.monotonic()
        poll_s = self._cfg.poll_start_ms / 1000
        scratch: dict = {}
        while True:
            met, evidence = await checker(cond, scratch)
            waited = int((time.monotonic() - start) * 1000)
            if met:
                return WaitResult(True, waited, evidence)
            if waited >= timeout_ms:
                return WaitResult(False, waited, evidence)
            await anyio.sleep(poll_s)
            poll_s = min(poll_s * 1.5, self._cfg.poll_max_ms / 1000)

    async def _text_present(self, cond: dict, scratch: dict):
        from ..types import Region
        region = (Region(**cond["region"]) if cond.get("region") else None)
        boxes = await self._ocr.recognize(region)
        matches = find_matching_boxes(boxes, str(cond.get("text", "")))
        evidence = {"matches": matches_to_evidence(matches)}
        return bool(matches), evidence

    async def _screen_stable(self, cond: dict, scratch: dict):
        quiet_ms = int(cond.get("quiet_ms", 500))
        shot = await self._shots.capture(fresh=True)
        now = time.monotonic()
        if scratch.get("phash") != shot.phash:
            scratch["phash"] = shot.phash
            scratch["since"] = now
            return False, {"phash": shot.phash}
        stable_ms = (now - scratch["since"]) * 1000
        return stable_ms >= quiet_ms, {"phash": shot.phash,
                                       "stable_ms": int(stable_ms)}

    async def _window_present(self, cond: dict, scratch: dict):
        wins = await anyio.to_thread.run_sync(self._driver.list_windows, True)
        app = str(cond.get("app", "")).lower()
        title = str(cond.get("title", "")).lower()
        matches = [w for w in wins
                   if (not app or app in (w.bundle_id or "").lower()
                       or app in w.app_name.lower())
                   and (not title or title in w.title.lower())]
        evidence = {"windows": [{"window_ref": w.window_ref, "title": w.title,
                                  "app": w.app_name} for w in matches]}
        return bool(matches), evidence

    async def _window_gone(self, cond: dict, scratch: dict):
        met, evidence = await self._window_present(cond, scratch)
        return not met, evidence

    async def _app_frontmost(self, cond: dict, scratch: dict):
        apps = await anyio.to_thread.run_sync(self._driver.running_apps)
        needle = str(cond.get("app", "")).lower()
        front = next((a for a in apps if a.frontmost), None)
        met = front is not None and needle in (
            (front.bundle_id or "").lower(), front.name.lower())
        return met, {"frontmost": front.name if front else None}
