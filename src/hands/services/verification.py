"""Verification engine: confidence-scored outcome checks with evidence
(DESIGN §4.16). The agent decides WHAT to verify; this engine only answers."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from ..config import HandsConfig
from ..driver.base import Driver
from ..errors import InvalidArgsError
from ..types import Point, Region
from .ocr import OCRService, find_matching_boxes, matches_to_evidence
from .screenshot import Screenshot, ScreenshotService
from .vision import crop, frame_diff

_KNOWN_TYPES = frozenset({
    "text_present", "text_absent", "region_changed", "region_unchanged",
    "cursor_at", "all_of", "any_of",
    # M3 extends: window_present, window_gone, clipboard_contains
})


@dataclass(frozen=True, slots=True)
class Expectation:
    type: str
    params: dict
    children: tuple["Expectation", ...] = ()

    @classmethod
    def from_wire(cls, raw: dict) -> "Expectation":
        t = raw.get("type")
        if t not in _KNOWN_TYPES:
            raise InvalidArgsError(
                f"unknown expectation type: {t!r}",
                details={"known": sorted(_KNOWN_TYPES)})
        children = tuple(cls.from_wire(c) for c in raw.get("children", []))
        params = {k: v for k, v in raw.items()
                  if k not in ("type", "children")}
        return cls(t, params, children)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    confidence: float
    evidence: dict
    failed_clauses: tuple[str, ...] = ()


class VerificationEngine:
    def __init__(self, screenshots: ScreenshotService, ocr: OCRService,
                 driver: Driver, config: HandsConfig) -> None:
        self._shots = screenshots
        self._ocr = ocr
        self._driver = driver
        self._cfg = config
        self._diff_threshold = config.verification.diff_threshold

    async def verify(self, expect: Expectation,
                     baseline: Screenshot | None = None
                     ) -> VerificationResult:
        shot = await self._shots.capture(fresh=True)
        return await self._eval(expect, shot, baseline)

    async def _eval(self, e: Expectation, shot: Screenshot,
                    baseline: Screenshot | None) -> VerificationResult:
        if e.type in ("all_of", "any_of"):
            results = [await self._eval(c, shot, baseline)
                       for c in e.children]
            if e.type == "all_of":
                passed = all(r.passed for r in results)
                confidence = min((r.confidence for r in results),
                                 default=1.0)
            else:
                passed = any(r.passed for r in results)
                confidence = max((r.confidence for r in results),
                                 default=0.0)
            failed = tuple(c.type for c, r in zip(e.children, results)
                           if not r.passed)
            return VerificationResult(
                passed, confidence,
                {"children": [r.evidence for r in results]}, failed)
        handler = getattr(self, f"_{e.type}")
        result = await handler(e.params, shot, baseline)
        if result.passed:
            return result
        return VerificationResult(result.passed, result.confidence,
                                  result.evidence, (e.type,))

    async def _text_present(self, params, shot, baseline):
        region = (Region(**params["region"])
                  if params.get("region") else None)
        boxes = await self._ocr.recognize(region)
        matches = find_matching_boxes(boxes, str(params.get("text", "")))
        best = max((b.confidence for b in matches), default=0.0)
        evidence = {"matches": matches_to_evidence(matches),
                    "seen": [b.text for b in boxes]}
        return VerificationResult(bool(matches), best, evidence)

    async def _text_absent(self, params, shot, baseline):
        inner = await self._text_present(params, shot, baseline)
        return VerificationResult(not inner.passed,
                                  1.0 - inner.confidence, inner.evidence)

    def _crop_region(self, shot: Screenshot, region_pt: Region):
        img = Image.open(io.BytesIO(shot.data))
        px = Region((region_pt.x - shot.bounds_pt.x) * shot.px_per_pt,
                    (region_pt.y - shot.bounds_pt.y) * shot.px_per_pt,
                    region_pt.width * shot.px_per_pt,
                    region_pt.height * shot.px_per_pt)
        return crop(img, px)

    async def _region_changed(self, params, shot, baseline):
        if baseline is None:
            raise InvalidArgsError(
                "region_changed requires baseline_screenshot_id")
        region = Region(**params["region"])
        diff = frame_diff(self._crop_region(baseline, region),
                          self._crop_region(shot, region))
        confidence = min(1.0, diff.changed_fraction / self._diff_threshold)
        return VerificationResult(
            diff.changed_fraction > self._diff_threshold, confidence,
            {"changed_fraction": diff.changed_fraction})

    async def _region_unchanged(self, params, shot, baseline):
        inner = await self._region_changed(params, shot, baseline)
        return VerificationResult(not inner.passed,
                                  1.0 - inner.confidence, inner.evidence)

    async def _cursor_at(self, params, shot, baseline):
        tolerance = float(params.get("tolerance", 3.0))
        cur = self._driver.cursor_position()
        target = Point(float(params["x"]), float(params["y"]))
        hit = (abs(cur.x - target.x) <= tolerance
               and abs(cur.y - target.y) <= tolerance)
        return VerificationResult(
            hit, 1.0 if hit else 0.0,
            {"cursor": {"x": cur.x, "y": cur.y}})
