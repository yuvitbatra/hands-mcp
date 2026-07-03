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

from ..errors import DriverError, PermissionMissingError
from ..types import (
    DisplayInfo,
    ModifierFlags,
    MouseButton,
    Point,
    Region,
)
from .base import MouseEventSpec, RawFrame, RawTextBox

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
