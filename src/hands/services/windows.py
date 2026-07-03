"""Window management with stale-ref recovery (DESIGN §4.8, §9.2)."""
from __future__ import annotations

import difflib

import anyio

from ..driver.base import Driver
from ..errors import InvalidArgsError, TargetNotFoundError
from ..types import Region, WindowInfo

FUZZY_TITLE_RATIO = 0.7


def _title_ratio(a: str, b: str) -> float:
    """Similarity of two titles, normalized by the shorter title's length.

    Window titles routinely grow by suffix/prefix (an app appends
    " — Edited", "(1)", a document-dirty marker, ...) rather than being
    rewritten wholesale. difflib's default ratio() normalizes by the SUM
    of both lengths, so a pure prefix match against a much longer title
    scores low even though the shorter title is fully contained. Matching
    against the shorter title's length instead keeps that common case a
    strong match while still penalizing genuinely unrelated titles.
    """
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b)
    matched = sum(block.size for block in sm.get_matching_blocks())
    return matched / min(len(a), len(b))


class WindowService:
    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._snapshots: dict[str, WindowInfo] = {}

    async def list(self, app: str | None = None,
                   on_screen_only: bool = True) -> list[WindowInfo]:
        wins = await anyio.to_thread.run_sync(
            self._driver.list_windows, on_screen_only)
        if app is not None:
            needle = app.lower()
            wins = [w for w in wins
                    if needle in (w.bundle_id or "").lower()
                    or needle in w.app_name.lower()]
        for w in wins:
            self._snapshots[w.window_ref] = w
        return wins

    async def focus(self, window_ref: str | None = None,
                    app: str | None = None,
                    title_match: str | None = None) -> WindowInfo:
        win = await self._resolve(window_ref, app, title_match)
        await anyio.to_thread.run_sync(
            self._driver.window_perform, win.window_ref, "raise", None)
        return await self._refresh(win.window_ref)

    async def manage(self, window_ref: str, action: str,
                     bounds: Region | None = None) -> WindowInfo:
        win = await self._resolve(window_ref, None, None)
        await anyio.to_thread.run_sync(
            self._driver.window_perform, win.window_ref, action, bounds)
        if action == "close":
            self._snapshots.pop(win.window_ref, None)
            return win
        return await self._refresh(win.window_ref)

    async def _refresh(self, ref: str) -> WindowInfo:
        for w in await self.list(on_screen_only=False):
            if w.window_ref == ref:
                return w
        raise TargetNotFoundError(f"window {ref} vanished after action")

    async def _resolve(self, window_ref: str | None, app: str | None,
                       title_match: str | None) -> WindowInfo:
        current = await self.list(on_screen_only=False)
        if window_ref is not None:
            for w in current:
                if w.window_ref == window_ref:
                    return w
            # Stale ref: fuzzy re-resolution against the last snapshot.
            old = self._snapshots.get(window_ref)
            if old is not None:
                best, best_ratio = None, 0.0
                for w in current:
                    if w.pid != old.pid:
                        continue
                    ratio = _title_ratio(old.title.lower(), w.title.lower())
                    if ratio > best_ratio:
                        best, best_ratio = w, ratio
                if best is not None and best_ratio >= FUZZY_TITLE_RATIO:
                    return best
            raise TargetNotFoundError(
                f"window {window_ref} not found",
                details={"candidates": [
                    {"window_ref": w.window_ref, "title": w.title,
                     "app": w.app_name} for w in current]},
                remediation="call window_list and pick a current ref")
        if app is None and title_match is None:
            raise InvalidArgsError(
                "provide window_ref, or app and/or title_match")
        candidates = current
        if app is not None:
            needle = app.lower()
            candidates = [w for w in candidates
                          if needle in (w.bundle_id or "").lower()
                          or needle in w.app_name.lower()]
        if title_match is not None:
            needle = title_match.lower()
            scored = sorted(
                ((_title_ratio(needle, w.title.lower()), w)
                 for w in candidates),
                key=lambda t: -t[0])
            candidates = [w for r, w in scored
                          if needle in w.title.lower()
                          or r >= FUZZY_TITLE_RATIO]
        if not candidates:
            raise TargetNotFoundError(
                f"no window matches app={app!r} title={title_match!r}",
                details={"candidates": [
                    {"window_ref": w.window_ref, "title": w.title,
                     "app": w.app_name} for w in current]})
        return candidates[0]
