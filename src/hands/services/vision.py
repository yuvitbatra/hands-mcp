"""Pure-Pillow image helpers; no OS dependencies (DESIGN §4.11)."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

from ..driver.base import RawFrame
from ..types import Region


def downscale(frame: RawFrame, max_dim: int) -> tuple[Image.Image, float]:
    img = frame.image
    long_edge = max(img.size)
    if long_edge <= max_dim:
        return img, frame.px_per_pt
    factor = max_dim / long_edge
    new_size = (round(img.width * factor), round(img.height * factor))
    return img.resize(new_size, Image.LANCZOS), frame.px_per_pt * factor


def encode(image: Image.Image, fmt: str, jpeg_quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpeg":
        image.convert("RGB").save(buf, "JPEG", quality=jpeg_quality)
    else:
        image.save(buf, "PNG")
    return buf.getvalue()


def perceptual_hash(image: Image.Image) -> str:
    """64-bit average hash: grayscale 8x8, threshold at mean."""
    small = image.convert("L").resize((8, 8), Image.LANCZOS)
    pixels = list(small.getdata())
    mean = sum(pixels) / 64
    bits = 0
    for i, p in enumerate(pixels):
        if p >= mean:
            bits |= 1 << i
    return f"{bits:016x}"


@dataclass(frozen=True, slots=True)
class DiffResult:
    changed_fraction: float
    changed_region: Region | None   # bounding box in image pixels


def frame_diff(a: Image.Image, b: Image.Image, threshold: int = 24) -> DiffResult:
    """Fraction of pixels whose grayscale delta exceeds threshold, plus the
    bounding box of the change (DESIGN §4.11)."""
    if a.size != b.size:
        w, h = b.size
        return DiffResult(1.0, Region(0, 0, w, h))
    delta = ImageChops.difference(a.convert("L"), b.convert("L"))
    mask = delta.point(lambda v: 255 if v > threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return DiffResult(0.0, None)
    changed = mask.histogram()[255]
    frac = changed / (a.size[0] * a.size[1])
    x0, y0, x1, y1 = bbox
    return DiffResult(frac, Region(x0, y0, x1 - x0, y1 - y0))


def crop(image: Image.Image, region_px: Region) -> Image.Image:
    return image.crop((round(region_px.x), round(region_px.y),
                       round(region_px.x + region_px.width),
                       round(region_px.y + region_px.height)))


def annotate(image: Image.Image, boxes: list[Region],
             color: tuple[int, int, int] = (255, 0, 0)) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    for b in boxes:
        draw.rectangle([b.x, b.y, b.x + b.width, b.y + b.height],
                       outline=color, width=3)
    return out
