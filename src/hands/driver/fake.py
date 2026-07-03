"""In-memory virtual desktop for tests (DESIGN §3.1, driver/fake.py)."""
from __future__ import annotations

from dataclasses import dataclass as _dataclass

from PIL import Image

from ..errors import InvalidArgsError, TargetNotFoundError
from ..types import (AppInfo, ClipboardContent, DisplayInfo, ModifierFlags,
                    Point, Region, WindowInfo)
from .base import MouseEventSpec, RawFrame, RawTextBox


@_dataclass
class _FakeWindow:
    number: int
    app_name: str
    bundle_id: str | None
    pid: int
    title: str
    bounds: Region
    focused: bool = False
    minimized: bool = False

    @property
    def ref(self) -> str:
        return f"{self.pid}:{self.number}"

    def to_info(self) -> WindowInfo:
        return WindowInfo(self.ref, self.app_name, self.bundle_id,
                          self.pid, self.title, self.bounds,
                          self.focused, self.minimized)


class FakeDriver:
    def __init__(self, size_pt: tuple[int, int] = (1440, 900),
                 scale: float = 2.0) -> None:
        w, h = size_pt
        self._display = DisplayInfo(display_id=1,
                                    bounds_pt=Region(0, 0, w, h),
                                    scale=scale, is_main=True)
        self._scale = scale
        self._cursor = Point(0, 0)
        self._screen = Image.new("RGB", (int(w * scale), int(h * scale)),
                                 (30, 30, 30))
        self.events: list[tuple] = []
        self._typed: list[str] = []
        self._fail_next: dict[str, Exception] = {}
        self._ocr_boxes: list[RawTextBox] = []
        self.ocr_calls = 0
        self._clipboard = ClipboardContent("empty")
        self._secure_input = False
        self._windows: list[_FakeWindow] = []
        self._next_window_number = 1
        self._installed: dict[str, str] = {}      # bundle_id -> name
        self._running: dict[int, dict] = {}       # pid -> {name, bundle_id, frontmost}
        self._next_pid = 1000

    # --- test helpers -----------------------------------------------------
    def fail_next(self, op: str, exc: Exception) -> None:
        self._fail_next[op] = exc

    def pop_events(self) -> list[tuple]:
        out, self.events = self.events, []
        return out

    def typed_text(self) -> str:
        return "".join(self._typed)

    def set_ocr_boxes(self, boxes: list[RawTextBox]) -> None:
        self._ocr_boxes = list(boxes)

    def set_secure_input(self, active: bool) -> None:
        self._secure_input = active

    def draw_rect(self, region_pt: Region,
                  color: tuple[int, int, int]) -> None:
        """Paint the virtual screen (test helper; coordinates in points)."""
        from PIL import ImageDraw
        s = self._scale
        ImageDraw.Draw(self._screen).rectangle(
            [region_pt.x * s, region_pt.y * s,
             (region_pt.x + region_pt.width) * s,
             (region_pt.y + region_pt.height) * s],
            fill=color)

    def _maybe_fail(self, op: str) -> None:
        exc = self._fail_next.pop(op, None)
        if exc is not None:
            raise exc

    # --- Driver protocol ----------------------------------------------------
    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame:
        self._maybe_fail("capture")
        if region is None:
            return RawFrame(self._screen.copy(), self._display.bounds_pt,
                            self._scale, self._display.display_id)
        s = self._scale
        box = (int(region.x * s), int(region.y * s),
               int((region.x + region.width) * s),
               int((region.y + region.height) * s))
        return RawFrame(self._screen.crop(box), region, s,
                        self._display.display_id)

    def displays(self) -> list[DisplayInfo]:
        return [self._display]

    def cursor_position(self) -> Point:
        return self._cursor

    def post_mouse(self, event: MouseEventSpec) -> None:
        self._maybe_fail("post_mouse")
        self.events.append(("mouse", event))
        self._cursor = event.at

    def post_scroll(self, at: Point, dx: int, dy: int, pixels: bool) -> None:
        self._maybe_fail("post_scroll")
        self.events.append(("scroll", at, dx, dy, pixels))

    def type_unicode(self, text: str) -> None:
        self._maybe_fail("type_unicode")
        self.events.append(("type", text))
        self._typed.append(text)

    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None:
        self._maybe_fail("post_key")
        self.events.append(("key", keycode, down, flags))

    def ocr(self, frame: RawFrame,
            languages: list[str]) -> list[RawTextBox]:
        self._maybe_fail("ocr")
        self.ocr_calls += 1
        return list(self._ocr_boxes)

    def clipboard_read(self) -> ClipboardContent:
        self._maybe_fail("clipboard_read")
        return self._clipboard

    def clipboard_write(self, content: ClipboardContent) -> None:
        self._maybe_fail("clipboard_write")
        self._clipboard = content
        self.events.append(("clipboard_write", content.kind))

    def secure_input_active(self) -> bool:
        return self._secure_input

    # --- window model ------------------------------------------------------
    def add_window(self, app_name: str, bundle_id: str | None, pid: int,
                   title: str, bounds: Region,
                   focused: bool = False) -> str:
        win = _FakeWindow(self._next_window_number, app_name, bundle_id,
                          pid, title, bounds, focused)
        self._next_window_number += 1
        if focused:
            for other in self._windows:
                other.focused = False
        self._windows.append(win)
        return win.ref

    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]:
        self._maybe_fail("list_windows")
        return [w.to_info() for w in self._windows
                if not (on_screen_only and w.minimized)]

    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None:
        self._maybe_fail("window_perform")
        win = next((w for w in self._windows if w.ref == window_ref), None)
        if win is None:
            raise TargetNotFoundError(
                f"window {window_ref} not found",
                details={"candidates": [w.ref for w in self._windows]})
        if action in ("move", "resize"):
            if bounds is None:
                raise InvalidArgsError(f"{action} requires bounds")
            win.bounds = bounds
        elif action == "minimize":
            win.minimized = True
            win.focused = False
        elif action == "unminimize":
            win.minimized = False
        elif action == "maximize":
            win.bounds = self._display.bounds_pt
        elif action == "raise":
            for other in self._windows:
                other.focused = False
            win.focused = True
            win.minimized = False
        elif action == "close":
            self._windows.remove(win)
        else:
            raise InvalidArgsError(f"unknown window action: {action}")
        self.events.append(("window", window_ref, action))

    # --- app model -----------------------------------------------------
    def install_app(self, name: str, bundle_id: str) -> None:
        self._installed[bundle_id] = name

    def _find_installed(self, ident: str) -> tuple[str, str] | None:
        for bid, name in self._installed.items():
            if ident in (bid, name):
                return bid, name
        return None

    def running_apps(self) -> list[AppInfo]:
        self._maybe_fail("running_apps")
        return [AppInfo(r["bundle_id"], r["name"], pid, r["frontmost"])
                for pid, r in self._running.items()]

    def launch_app(self, ident: str) -> AppInfo:
        self._maybe_fail("launch_app")
        for pid, r in self._running.items():
            if ident in (r["bundle_id"], r["name"]):
                self.activate_app(pid)
                return AppInfo(r["bundle_id"], r["name"], pid, True)
        found = self._find_installed(ident)
        if found is None:
            raise TargetNotFoundError(
                f"no such app: {ident}",
                details={"installed": sorted(self._installed)})
        bid, name = found
        pid = self._next_pid
        self._next_pid += 1
        for r in self._running.values():
            r["frontmost"] = False
        self._running[pid] = {"name": name, "bundle_id": bid,
                              "frontmost": True}
        self.add_window(name, bid, pid, name,
                        Region(50, 50, 1000, 700), focused=True)
        self.events.append(("app", pid, "launch"))
        return AppInfo(bid, name, pid, True)

    def activate_app(self, pid: int) -> None:
        self._maybe_fail("activate_app")
        if pid not in self._running:
            raise TargetNotFoundError(f"pid {pid} not running")
        for p, r in self._running.items():
            r["frontmost"] = (p == pid)
        for w in self._windows:
            w.focused = (w.pid == pid)
        self.events.append(("app", pid, "activate"))

    def terminate_app(self, pid: int, force: bool) -> None:
        self._maybe_fail("terminate_app")
        if pid not in self._running:
            raise TargetNotFoundError(f"pid {pid} not running")
        del self._running[pid]
        self._windows = [w for w in self._windows if w.pid != pid]
        self.events.append(
            ("app", pid, "force_terminate" if force else "terminate"))
