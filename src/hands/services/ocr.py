"""OCR provider: driver recognition + coordinate normalization + caching
(DESIGN §4.10). The driver returns Vision-style normalized boxes; nothing
outside this module ever sees a bottom-left-origin coordinate."""
from __future__ import annotations

from collections import OrderedDict

import anyio

from ..config import HandsConfig
from ..driver.base import Driver
from ..types import Region, TextBox
from .coords import CoordinateMapper
from .vision import perceptual_hash


def find_matching_boxes(boxes: list[TextBox], query: str) -> list[TextBox]:
    """Return the boxes whose text contains `query` as a case-insensitive
    substring. This is the exact-match semantics shared by `Waiter` and
    `VerificationEngine` (as opposed to `find_text`'s fuzzy matching)."""
    q = query.lower()
    return [b for b in boxes if q in b.text.lower()]


def matches_to_evidence(matches: list[TextBox]) -> list[dict]:
    """Shared evidence-dict shape for OCR text matches."""
    return [
        {"text": b.text, "confidence": b.confidence,
         "center": {"x": b.region.center.x, "y": b.region.center.y}}
        for b in matches
    ]


class OCRService:
    def __init__(self, driver: Driver, coords: CoordinateMapper,
                 config: HandsConfig) -> None:
        self._driver = driver
        self._coords = coords
        self._cfg = config.ocr
        self._cache: OrderedDict[str, list[TextBox]] = OrderedDict()

    async def recognize(self, region: Region | None = None,
                        languages: list[str] | None = None
                        ) -> list[TextBox]:
        langs = languages or self._cfg.languages
        frame = await anyio.to_thread.run_sync(
            self._driver.capture, region, None)
        key = f"{perceptual_hash(frame.image)}:{frame.bounds_pt}:{langs}"
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        raw = await anyio.to_thread.run_sync(self._driver.ocr, frame, langs)
        boxes = [
            TextBox(
                text=r.text,
                region=self._coords.vision_normalized_to_pt(
                    r.nx, r.ny, r.nw, r.nh, frame.bounds_pt),
                confidence=r.confidence,
            )
            for r in raw
        ]
        self._cache[key] = boxes
        while len(self._cache) > self._cfg.cache_size:
            self._cache.popitem(last=False)
        return boxes
