"""Application lifecycle (DESIGN §4.9)."""
from __future__ import annotations

import anyio

from ..driver.base import Driver
from ..errors import TargetNotFoundError, ToolTimeoutError
from ..types import AppInfo, WindowInfo


class AppService:
    def __init__(self, driver: Driver, waiter) -> None:
        self._driver = driver
        self._waiter = waiter

    async def list_running(self) -> list[AppInfo]:
        return await anyio.to_thread.run_sync(self._driver.running_apps)

    async def _find_running(self, ident: str) -> AppInfo | None:
        needle = ident.lower()
        for a in await self.list_running():
            if needle in ((a.bundle_id or "").lower(), a.name.lower()):
                return a
        return None

    async def open(self, app: str, wait_for_window: bool = True,
                   timeout_ms: int = 15000
                   ) -> tuple[AppInfo, WindowInfo | None]:
        running = await self._find_running(app)
        if running is not None:
            await anyio.to_thread.run_sync(
                self._driver.activate_app, running.pid)
            info = await self._find_running(app)
        else:
            info = await anyio.to_thread.run_sync(
                self._driver.launch_app, app)
        window: WindowInfo | None = None
        if wait_for_window:
            res = await self._waiter.wait_for(
                {"type": "window_present", "app": app}, timeout_ms)
            if not res.met:
                raise ToolTimeoutError(
                    f"{app} produced no window within {timeout_ms} ms",
                    details={"app": app})
            wins = [w for w in await anyio.to_thread.run_sync(
                        self._driver.list_windows, True)
                    if w.pid == info.pid]
            window = wins[0] if wins else None
        return info, window

    async def close(self, app: str, force: bool = False) -> None:
        running = await self._find_running(app)
        if running is None:
            raise TargetNotFoundError(f"{app} is not running")
        await anyio.to_thread.run_sync(
            self._driver.terminate_app, running.pid, force)
