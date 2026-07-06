"""Real macOS driver: Quartz events + screencapture CLI (DESIGN §3.1).

M1 captures via /usr/sbin/screencapture — slower (~150 ms) than
ScreenCaptureKit but dependency-light and reliable; SCK lands in M2.
Requires TCC grants: Screen Recording (capture) and Accessibility
(event posting).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import Quartz
import structlog
from PIL import Image

from ..errors import (
    DriverError,
    InvalidArgsError,
    PermissionMissingError,
    TargetNotFoundError,
)
from ..types import (
    AppInfo,
    ClipboardContent,
    DisplayInfo,
    ModifierFlags,
    MouseButton,
    Point,
    Region,
    WindowInfo,
)
from .base import AXNode, MouseEventSpec, OSPermissions, RawFrame, RawTextBox

log = structlog.get_logger(__name__)

_FLAG_MASKS = {
    ModifierFlags.CMD: Quartz.kCGEventFlagMaskCommand,
    ModifierFlags.SHIFT: Quartz.kCGEventFlagMaskShift,
    ModifierFlags.ALT: Quartz.kCGEventFlagMaskAlternate,
    ModifierFlags.CTRL: Quartz.kCGEventFlagMaskControl,
}

_CG_BUTTONS = {
    MouseButton.LEFT: Quartz.kCGMouseButtonLeft,
    MouseButton.RIGHT: Quartz.kCGMouseButtonRight,
    MouseButton.MIDDLE: Quartz.kCGMouseButtonCenter,
}

_DOWN = {MouseButton.LEFT: Quartz.kCGEventLeftMouseDown,
         MouseButton.RIGHT: Quartz.kCGEventRightMouseDown,
         MouseButton.MIDDLE: Quartz.kCGEventOtherMouseDown}
_UP = {MouseButton.LEFT: Quartz.kCGEventLeftMouseUp,
       MouseButton.RIGHT: Quartz.kCGEventRightMouseUp,
       MouseButton.MIDDLE: Quartz.kCGEventOtherMouseUp}
_DRAG = {MouseButton.LEFT: Quartz.kCGEventLeftMouseDragged,
         MouseButton.RIGHT: Quartz.kCGEventRightMouseDragged,
         MouseButton.MIDDLE: Quartz.kCGEventOtherMouseDragged}


def _cg_flags(mods: ModifierFlags) -> int:
    flags = 0
    for flag, mask in _FLAG_MASKS.items():
        if flag in mods:
            flags |= mask
    return flags


class MacOSDriver:
    def __init__(self) -> None:
        self._pressed: set[MouseButton] = set()

    # --- perception ---------------------------------------------------------
    def displays(self) -> list[DisplayInfo]:
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        if err != 0:
            raise DriverError(f"CGGetActiveDisplayList failed: {err}")
        main_id = Quartz.CGMainDisplayID()
        out: list[DisplayInfo] = []
        for did in ids[:count]:
            b = Quartz.CGDisplayBounds(did)
            scale = Quartz.CGDisplayPixelsWide(did) / b.size.width
            out.append(DisplayInfo(
                display_id=int(did),
                bounds_pt=Region(b.origin.x, b.origin.y,
                                 b.size.width, b.size.height),
                scale=float(scale),
                is_main=(did == main_id)))
        return out

    def capture(self, region: Region | None,
                display_id: int | None) -> RawFrame:
        try:
            return self._capture_sck(region, display_id)
        except Exception:
            log.warning("sck_capture_failed_falling_back", exc_info=True)
            return self._capture_cli(region, display_id)

    def _capture_sck(self, region: Region | None,
                     display_id: int | None) -> RawFrame:
        """ScreenCaptureKit screenshot (DESIGN §4.4). Captures the full
        display, then crops the region in pixels."""
        import threading

        import ScreenCaptureKit as SCK
        from Quartz import (
            CGDataProviderCopyData,
            CGImageGetBytesPerRow,
            CGImageGetDataProvider,
            CGImageGetHeight,
            CGImageGetWidth,
        )

        box: dict = {}
        done = threading.Event()

        def content_cb(content, error):
            box["content"], box["error"] = content, error
            done.set()

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(
            content_cb)
        if not done.wait(2.0) or box.get("error") is not None:
            raise DriverError(f"SCShareableContent: {box.get('error')}")
        displays = box["content"].displays()
        target = None
        for d in displays:
            if display_id is None or d.displayID() == display_id:
                target = d
                break
        if target is None:
            raise DriverError(f"display {display_id} not found via SCK")

        filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            target, [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        scale = self._display_scale(target.displayID())
        cfg.setWidth_(int(target.width() * scale))
        cfg.setHeight_(int(target.height() * scale))
        cfg.setShowsCursor_(False)

        done2 = threading.Event()

        def shot_cb(image, error):
            box["image"], box["shot_error"] = image, error
            done2.set()

        SCK.SCScreenshotManager.\
            captureImageWithFilter_configuration_completionHandler_(
                filt, cfg, shot_cb)
        if not done2.wait(2.0) or box.get("shot_error") is not None:
            raise DriverError(f"SCScreenshotManager: {box.get('shot_error')}")

        cg = box["image"]
        w, h = CGImageGetWidth(cg), CGImageGetHeight(cg)
        bpr = CGImageGetBytesPerRow(cg)
        data = bytes(CGDataProviderCopyData(CGImageGetDataProvider(cg)))
        img = Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA",
                               bpr, 1).convert("RGB")

        display = self._display_info(target.displayID())
        px_per_pt = w / display.bounds_pt.width
        if region is None:
            return RawFrame(img, display.bounds_pt, px_per_pt,
                            display.display_id)
        crop_box = (
            int((region.x - display.bounds_pt.x) * px_per_pt),
            int((region.y - display.bounds_pt.y) * px_per_pt),
            int((region.x - display.bounds_pt.x + region.width) * px_per_pt),
            int((region.y - display.bounds_pt.y + region.height)
                * px_per_pt))
        return RawFrame(img.crop(crop_box), region, px_per_pt,
                        display.display_id)

    def _display_scale(self, display_id: int) -> float:
        return self._display_info(display_id).scale

    def _display_info(self, display_id: int) -> DisplayInfo:
        return next(d for d in self.displays()
                    if d.display_id == display_id)

    def _capture_cli(self, region: Region | None,
                display_id: int | None) -> RawFrame:
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionMissingError(
                "Screen Recording permission is not granted",
                remediation=("Enable this app under System Settings > "
                             "Privacy & Security > Screen Recording, "
                             "then restart the server"))
        main = next(d for d in self.displays() if d.is_main)
        bounds = region if region is not None else main.bounds_pt
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "shot.png"
            cmd = ["/usr/sbin/screencapture", "-x"]
            if region is not None:
                cmd += ["-R", f"{region.x},{region.y},"
                              f"{region.width},{region.height}"]
            cmd.append(str(path))
            proc = subprocess.run(cmd, capture_output=True, timeout=10)
            if proc.returncode != 0 or not path.exists():
                raise DriverError(
                    "screencapture failed",
                    details={"stderr": proc.stderr.decode().strip()})
            img = Image.open(path)
            img.load()   # read before the temp dir vanishes
        return RawFrame(img, bounds, img.width / bounds.width,
                        main.display_id)

    def cursor_position(self) -> Point:
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return Point(loc.x, loc.y)

    # --- OCR (Apple Vision; DESIGN §4.10) --------------------------------
    def ocr(self, frame: RawFrame,
            languages: list[str]) -> list[RawTextBox]:
        import io

        import Vision

        buf = io.BytesIO()
        frame.image.save(buf, "PNG")
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            buf.getvalue(), None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(
            Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(languages)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise DriverError(f"Vision OCR failed: {err}")
        out: list[RawTextBox] = []
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            top = candidates[0]
            bb = obs.boundingBox()
            out.append(RawTextBox(
                str(top.string()),
                float(bb.origin.x), float(bb.origin.y),
                float(bb.size.width), float(bb.size.height),
                float(top.confidence())))
        return out

    # --- input --------------------------------------------------------------
    def post_mouse(self, event: MouseEventSpec) -> None:
        if event.kind == "down":
            etype = _DOWN[event.button]
        elif event.kind == "up":
            etype = _UP[event.button]
        elif self._pressed:
            etype = _DRAG[next(iter(self._pressed))]
        else:
            etype = Quartz.kCGEventMouseMoved
        cg = Quartz.CGEventCreateMouseEvent(
            None, etype, (event.at.x, event.at.y), _CG_BUTTONS[event.button])
        if cg is None:
            raise DriverError("CGEventCreateMouseEvent returned None")
        if event.kind in ("down", "up"):
            Quartz.CGEventSetIntegerValueField(
                cg, Quartz.kCGMouseEventClickState, event.click_count)
        flags = _cg_flags(event.modifiers)
        if flags:
            Quartz.CGEventSetFlags(cg, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)
        if event.kind == "down":
            self._pressed.add(event.button)
        elif event.kind == "up":
            self._pressed.discard(event.button)

    def post_scroll(self, at: Point, dx: int, dy: int,
                    pixels: bool) -> None:
        unit = (Quartz.kCGScrollEventUnitPixel if pixels
                else Quartz.kCGScrollEventUnitLine)
        cg = Quartz.CGEventCreateScrollWheelEvent(None, unit, 2, dy, dx)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    def type_unicode(self, text: str) -> None:
        # Layout-independent unicode injection (DESIGN §4.6).
        for down in (True, False):
            cg = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(cg, len(text), text)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    def post_key(self, keycode: int, down: bool,
                 flags: ModifierFlags) -> None:
        cg = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        mask = _cg_flags(flags)
        if mask and down:
            Quartz.CGEventSetFlags(cg, mask)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cg)

    # --- clipboard (DESIGN §4.7) -----------------------------------------
    def clipboard_read(self) -> ClipboardContent:
        from AppKit import (
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
        )
        pb = NSPasteboard.generalPasteboard()
        text = pb.stringForType_(NSPasteboardTypeString)
        if text is not None:
            return ClipboardContent("text", text=str(text))
        data = pb.dataForType_(NSPasteboardTypePNG)
        if data is not None:
            return ClipboardContent("image", image_png=bytes(data))
        return ClipboardContent("empty")

    def clipboard_write(self, content: ClipboardContent) -> None:
        from AppKit import (
            NSData,
            NSPasteboard,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
        )
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        if content.kind == "text" and content.text is not None:
            pb.setString_forType_(content.text, NSPasteboardTypeString)
        elif content.kind == "image" and content.image_png is not None:
            pb.setData_forType_(
                NSData.dataWithBytes_length_(content.image_png,
                                             len(content.image_png)),
                NSPasteboardTypePNG)

    def secure_input_active(self) -> bool:
        import ctypes
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon")
        return bool(carbon.IsSecureEventInputEnabled())

    # --- windows (DESIGN §4.8) -------------------------------------------
    def list_windows(self, on_screen_only: bool) -> list[WindowInfo]:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionAll,
            kCGWindowListOptionOnScreenOnly,
        )
        opts = kCGWindowListExcludeDesktopElements
        opts |= (kCGWindowListOptionOnScreenOnly if on_screen_only
                 else kCGWindowListOptionAll)
        raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) or []
        apps = {a.pid: a for a in self.running_apps()}
        front_pid = next((a.pid for a in apps.values() if a.frontmost),
                         None)
        out: list[WindowInfo] = []
        for w in raw:
            if w.get("kCGWindowLayer", 0) != 0:
                continue                    # skip menubar/dock layers
            pid = int(w["kCGWindowOwnerPID"])
            b = w["kCGWindowBounds"]
            app = apps.get(pid)
            out.append(WindowInfo(
                window_ref=f"{pid}:{int(w['kCGWindowNumber'])}",
                app_name=str(w.get("kCGWindowOwnerName", "")),
                bundle_id=app.bundle_id if app else None,
                pid=pid,
                title=str(w.get("kCGWindowName", "") or ""),
                bounds=Region(float(b["X"]), float(b["Y"]),
                              float(b["Width"]), float(b["Height"])),
                focused=(pid == front_pid),
                minimized=not w.get("kCGWindowIsOnscreen", True)))
        return out

    def _ax_window_for_ref(self, window_ref: str):
        """Resolve 'pid:number' to an AXUIElement window by title+bounds
        proximity (AX has no CGWindowNumber bridge; DESIGN §4.8)."""
        import ApplicationServices as AS
        pid = int(window_ref.split(":")[0])
        target = next((w for w in self.list_windows(False)
                       if w.window_ref == window_ref), None)
        if target is None:
            raise TargetNotFoundError(f"window {window_ref} not found")
        app_el = AS.AXUIElementCreateApplication(pid)
        err, windows = AS.AXUIElementCopyAttributeValue(
            app_el, AS.kAXWindowsAttribute, None)
        if err != 0 or not windows:
            raise TargetNotFoundError(
                f"no AX windows for pid {pid}",
                details={"ax_error": int(err)})
        for el in windows:
            _, title = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXTitleAttribute, None)
            if str(title or "") == target.title:
                return el, target
        return windows[0], target        # single-window fallback

    def window_perform(self, window_ref: str, action: str,
                       bounds: Region | None) -> None:
        import ApplicationServices as AS
        import Quartz
        el, info = self._ax_window_for_ref(window_ref)
        if action in ("move", "resize", "maximize"):
            if action == "maximize":
                bounds = self.displays()[0].bounds_pt
            if action in ("move", "maximize"):
                point = Quartz.CGPoint(bounds.x, bounds.y)
                value = AS.AXValueCreate(AS.kAXValueCGPointType, point)
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXPositionAttribute, value)
            if action in ("resize", "maximize"):
                size = Quartz.CGSize(bounds.width, bounds.height)
                value = AS.AXValueCreate(AS.kAXValueCGSizeType, size)
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXSizeAttribute, value)
        elif action in ("minimize", "unminimize"):
            AS.AXUIElementSetAttributeValue(
                el, AS.kAXMinimizedAttribute,
                action == "minimize")
        elif action == "raise":
            self.activate_app(info.pid)
            AS.AXUIElementPerformAction(el, AS.kAXRaiseAction)
        elif action == "close":
            err, button = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXCloseButtonAttribute, None)
            if err != 0 or button is None:
                raise DriverError(f"window {window_ref} has no close "
                                  f"button (ax_error={int(err)})")
            AS.AXUIElementPerformAction(button, AS.kAXPressAction)
        else:
            raise InvalidArgsError(f"unknown window action: {action}")

    # --- apps (DESIGN §4.9) ------------------------------------------------
    def running_apps(self) -> list[AppInfo]:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        out = []
        for a in ws.runningApplications():
            if a.activationPolicy() != 0:      # regular apps only
                continue
            out.append(AppInfo(
                bundle_id=str(a.bundleIdentifier() or "") or None,
                name=str(a.localizedName() or ""),
                pid=int(a.processIdentifier()),
                frontmost=(front is not None
                           and a.processIdentifier()
                           == front.processIdentifier())))
        return out

    def launch_app(self, ident: str) -> AppInfo:
        import subprocess
        import time
        for a in self.running_apps():
            if ident in ((a.bundle_id or ""), a.name):
                self.activate_app(a.pid)
                return AppInfo(a.bundle_id, a.name, a.pid, True)
        flag = "-b" if "." in ident else "-a"
        proc = subprocess.run(["/usr/bin/open", flag, ident],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise TargetNotFoundError(
                f"cannot launch {ident}: {proc.stderr.strip()}")
        deadline = time.time() + 15
        while time.time() < deadline:
            for a in self.running_apps():
                if ident in ((a.bundle_id or ""), a.name):
                    return a
            time.sleep(0.2)
        raise DriverError(f"{ident} did not appear in running apps")

    def activate_app(self, pid: int) -> None:
        from AppKit import (
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )
        app = NSRunningApplication.\
            runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            raise TargetNotFoundError(f"pid {pid} not running")
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    def terminate_app(self, pid: int, force: bool) -> None:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.\
            runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            raise TargetNotFoundError(f"pid {pid} not running")
        if force:
            app.forceTerminate()
        else:
            app.terminate()

    # --- AX tree (DESIGN §5.15) --------------------------------------------
    def ax_tree(self, pid: int | None, max_depth: int) -> AXNode:
        import ApplicationServices as AS
        if not AS.AXIsProcessTrusted():
            raise PermissionMissingError(
                "Accessibility permission missing",
                remediation="x-apple.systempreferences:com.apple."
                            "preference.security?Privacy_Accessibility")
        if pid is None:
            front = next((a for a in self.running_apps()
                          if a.frontmost), None)
            if front is None:
                raise TargetNotFoundError("no frontmost app")
            pid = front.pid
        root = AS.AXUIElementCreateApplication(pid)

        def attr(el, name):
            err, value = AS.AXUIElementCopyAttributeValue(el, name, None)
            return value if err == 0 else None

        def walk(el, depth: int) -> AXNode:
            role = str(attr(el, AS.kAXRoleAttribute) or "AXUnknown")
            title = attr(el, AS.kAXTitleAttribute)
            value = attr(el, AS.kAXValueAttribute)
            region = None
            pos = attr(el, AS.kAXPositionAttribute)
            size = attr(el, AS.kAXSizeAttribute)
            if pos is not None and size is not None:
                ok_p, point = AS.AXValueGetValue(
                    pos, AS.kAXValueCGPointType, None)
                ok_s, sz = AS.AXValueGetValue(
                    size, AS.kAXValueCGSizeType, None)
                if ok_p and ok_s:
                    region = Region(point.x, point.y,
                                    sz.width, sz.height)
            err, actions = AS.AXUIElementCopyActionNames(el, None)
            children: tuple[AXNode, ...] = ()
            if depth > 1:
                kids = attr(el, AS.kAXChildrenAttribute) or []
                children = tuple(walk(k, depth - 1) for k in kids)
            return AXNode(role,
                          str(title) if title is not None else None,
                          str(value) if value is not None else None,
                          region,
                          tuple(str(a) for a in (actions or [])),
                          children)

        return walk(root, max_depth)

    # --- TCC (DESIGN §4.19) --------------------------------------------------
    def permissions(self) -> OSPermissions:
        import ApplicationServices as AS
        from Quartz import CGPreflightScreenCaptureAccess
        return OSPermissions(
            screen_recording=bool(CGPreflightScreenCaptureAccess()),
            accessibility=bool(AS.AXIsProcessTrusted()))
