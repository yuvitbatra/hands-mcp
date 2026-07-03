# Hands Milestone 2 — Perception & Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OCR-grounded perception — `find_text` returns clickable text boxes, `wait` becomes condition-based, `verify` checks outcomes with visual evidence — plus Apple Vision OCR and ScreenCaptureKit capture in the macOS driver.

**Architecture:** Extends M1's layers without changing them: new pure-Pillow vision utilities, an `ocr` method on the `Driver` protocol (fake returns scripted boxes; macOS uses Vision.framework), an `OCRService` that converts Vision's bottom-left normalized boxes into canonical points via `CoordinateMapper`, a polling `Waiter`, and a strategy-table `VerificationEngine`. Three tools are added/upgraded in `tools/observe.py`.

**Tech Stack:** Same as M1 (Python ≥ 3.12, uv, `mcp` low-level Server, Pydantic v2, anyio, Pillow, structlog, pytest). macOS additions: `pyobjc-framework-Vision`, `pyobjc-framework-ScreenCaptureKit`.

## Milestone map (context, not tasks)

- **M1 (done, prerequisite):** errors/types/config/retry, fake driver, screenshot/mouse/keyboard services, registry + dispatcher, 9 tools, server/CLI, macOS driver v1 (`screencapture` CLI capture).
- **M2 (this plan):** frame diff/crop/annotate, `TextBox` + Vision coordinate conversion, driver OCR surface, `OCRService`, `find_text`, `Waiter` + condition-based `wait`, `VerificationEngine` + `verify`, macOS Vision OCR + ScreenCaptureKit capture.
- **M3 (future plan):** clipboard/windows/apps services + tools, AX tree, rule-based `PermissionEngine` + confirmation hooks, hash-chained audit, metrics/doctor/permissions CLI.
- **M4 (future plan):** plugin system, `execute_sequence`, e2e fixture app, perf/stress suites.

## Global Constraints

- **M1 plan (`2026-07-03-hands-m1-core.md`) must be fully implemented and green (`uv run pytest -q`) before starting.**
- Python `>=3.12`; `src/` layout; package `hands`; managed with `uv`.
- **No git commits for now (user instruction, 2026-07-03).** Tasks end with a "Verify" step running the full test suite instead of a commit. When the user lifts this, commit once per completed task with `feat:`/`test:` prefixes.
- All coordinates everywhere are **logical points, top-left origin of the main display, y-down** (DESIGN §4.12). Vision.framework boxes are normalized with a **bottom-left** origin; the flip happens in exactly one place: `CoordinateMapper.vision_normalized_to_pt`.
- `stdout` is reserved for the MCP transport. All logging to `stderr`. Never `print()` in library code.
- Use `anyio`; blocking driver calls (pyobjc, Vision) run via `anyio.to_thread.run_sync`.
- Pydantic argument models use `extra="forbid"`.
- Error codes are the stable wire contract (M1 list); this milestone adds no new codes.
- All new read tools: `policy_class="read"`, `RetryPolicy.read()`, `idempotent=True`.
- macOS-only tests are gated by `HANDS_CONTRACT_MACOS=1` exactly like M1 Task 16.

---

### Task 1: Frame diff, crop, annotate (`services/vision.py`)

**Files:**
- Modify: `src/hands/services/vision.py` (append; keep M1's `downscale`, `encode`, `perceptual_hash` untouched)
- Test: `tests/unit/test_vision_diff.py`

**Interfaces:**
- Consumes: `Region` (M1 Task 3).
- Produces:
  - `DiffResult(changed_fraction: float, changed_region: Region | None)` frozen dataclass — `changed_region` is a bounding box **in image pixels** of the changed area, `None` when nothing changed.
  - `frame_diff(a: PIL.Image.Image, b: PIL.Image.Image, threshold: int = 24) -> DiffResult` — grayscale absolute difference; pixels with delta > `threshold` count as changed; mismatched sizes → full change.
  - `crop(image: PIL.Image.Image, region_px: Region) -> PIL.Image.Image`
  - `annotate(image: PIL.Image.Image, boxes: list[Region], color: tuple[int, int, int] = (255, 0, 0)) -> PIL.Image.Image` — returns a **copy** with 3-px rectangle outlines.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_vision_diff.py`:

```python
from PIL import Image, ImageDraw

from hands.services.vision import annotate, crop, frame_diff
from hands.types import Region


def _img(color, size=(200, 100)):
    return Image.new("RGB", size, color)


def test_identical_frames_no_change():
    d = frame_diff(_img((255, 255, 255)), _img((255, 255, 255)))
    assert d.changed_fraction == 0.0
    assert d.changed_region is None


def test_left_half_changed():
    a = _img((255, 255, 255))
    b = _img((255, 255, 255))
    ImageDraw.Draw(b).rectangle([0, 0, 99, 99], fill=(0, 0, 0))
    d = frame_diff(a, b)
    assert 0.4 < d.changed_fraction < 0.6
    assert d.changed_region.x == 0
    assert d.changed_region.width <= 101


def test_mismatched_sizes_is_full_change():
    d = frame_diff(_img((0, 0, 0), (10, 10)), _img((0, 0, 0), (20, 20)))
    assert d.changed_fraction == 1.0
    assert d.changed_region == Region(0, 0, 20, 20)


def test_small_delta_below_threshold_ignored():
    d = frame_diff(_img((100, 100, 100)), _img((110, 110, 110)))
    assert d.changed_fraction == 0.0


def test_crop_dimensions():
    out = crop(_img((10, 20, 30)), Region(10, 5, 50, 40))
    assert out.size == (50, 40)


def test_annotate_draws_on_a_copy():
    base = _img((255, 255, 255))
    out = annotate(base, [Region(10, 10, 50, 20)])
    assert out is not base
    assert out.getpixel((10, 10)) != (255, 255, 255)
    assert base.getpixel((10, 10)) == (255, 255, 255)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_vision_diff.py -q`
Expected: FAIL — `ImportError: cannot import name 'frame_diff'`.

- [ ] **Step 3: Implement** — append to `src/hands/services/vision.py`:

```python
from dataclasses import dataclass

from PIL import ImageChops, ImageDraw

from ..types import Region


@dataclass(frozen=True, slots=True)
class DiffResult:
    changed_fraction: float
    changed_region: Region | None   # bounding box in image pixels


def frame_diff(a, b, threshold: int = 24) -> DiffResult:
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


def crop(image, region_px: Region):
    return image.crop((round(region_px.x), round(region_px.y),
                       round(region_px.x + region_px.width),
                       round(region_px.y + region_px.height)))


def annotate(image, boxes: list[Region],
             color: tuple[int, int, int] = (255, 0, 0)):
    out = image.copy()
    draw = ImageDraw.Draw(out)
    for b in boxes:
        draw.rectangle([b.x, b.y, b.x + b.width, b.y + b.height],
                       outline=color, width=3)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_vision_diff.py -q`
Expected: 6 passed.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass (M1 suite untouched).

---

### Task 2: `TextBox` and Vision coordinate conversion

**Files:**
- Modify: `src/hands/types.py` (append `TextBox`)
- Modify: `src/hands/services/coords.py` (append method)
- Test: `tests/unit/test_types.py` (append), `tests/unit/test_coords.py` (append)

**Interfaces:**
- Consumes: `Point`, `Region` (M1 Task 3); `CoordinateMapper` (M1 Task 9).
- Produces:
  - `TextBox(text: str, region: Region, confidence: float)` frozen dataclass in `hands.types`.
  - `CoordinateMapper.vision_normalized_to_pt(nx: float, ny: float, nw: float, nh: float, frame_bounds: Region) -> Region` — Vision boxes are normalized [0,1] with a **bottom-left** origin relative to the frame; result is a canonical top-left-origin point Region.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_coords.py`:

```python
def test_vision_normalized_flips_y(mapper):
    # Full-display frame (main display is 1440x900 in the mapper fixture).
    r = mapper.vision_normalized_to_pt(0.25, 0.10, 0.50, 0.20,
                                       Region(0, 0, 1440, 900))
    assert r.x == pytest.approx(360)
    assert r.width == pytest.approx(720)
    assert r.height == pytest.approx(180)
    # bottom edge at 0.10 * 900 from the bottom -> top edge at
    # (1 - 0.10 - 0.20) * 900 = 630 in y-down points.
    assert r.y == pytest.approx(630)


def test_vision_normalized_respects_frame_offset(mapper):
    r = mapper.vision_normalized_to_pt(0.0, 0.0, 1.0, 1.0,
                                       Region(100, 50, 200, 100))
    assert (r.x, r.y, r.width, r.height) == (100, 50, 200, 100)
```

And append to `tests/unit/test_types.py`:

```python
def test_textbox_center_is_clickable():
    from hands.types import TextBox

    box = TextBox("Submit", Region(100, 200, 80, 20), 0.98)
    assert box.region.center == Point(140, 210)
    assert box.confidence == 0.98
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_coords.py tests/unit/test_types.py -q`
Expected: FAIL — `AttributeError: ... vision_normalized_to_pt` and `ImportError: TextBox`.

- [ ] **Step 3: Implement**

Append to `src/hands/types.py`:

```python
@dataclass(frozen=True, slots=True)
class TextBox:
    """One OCR result in canonical point coordinates (DESIGN §4.10)."""
    text: str
    region: Region
    confidence: float          # 0..1
```

Append to `CoordinateMapper` in `src/hands/services/coords.py`:

```python
    def vision_normalized_to_pt(self, nx: float, ny: float, nw: float,
                                nh: float, frame_bounds: Region) -> Region:
        """Vision.framework boxes are normalized with a BOTTOM-LEFT origin;
        flip y and scale into canonical points (DESIGN §4.12)."""
        return Region(
            frame_bounds.x + nx * frame_bounds.width,
            frame_bounds.y + (1.0 - ny - nh) * frame_bounds.height,
            nw * frame_bounds.width,
            nh * frame_bounds.height,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_coords.py tests/unit/test_types.py -q`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 3: Driver OCR surface and fake-screen drawing

**Files:**
- Modify: `src/hands/driver/base.py` (append `RawTextBox`, extend `Driver` protocol)
- Modify: `src/hands/driver/fake.py` (OCR scripting + `draw_rect` + call counter)
- Modify: `src/hands/config.py` (add `ocr` and `waiter` sections)
- Test: `tests/unit/test_fake_driver.py` (append), `tests/unit/test_config.py` (append)

**Interfaces:**
- Consumes: M1 driver module (`RawFrame`, `FakeDriver` internals: `_maybe_fail`, `_screen`, `_scale`).
- Produces:
  - `RawTextBox(text: str, nx: float, ny: float, nw: float, nh: float, confidence: float)` frozen dataclass — Vision-style normalized, bottom-left origin, **relative to the frame it was recognized in**.
  - `Driver.ocr(frame: RawFrame, languages: list[str]) -> list[RawTextBox]` added to the protocol.
  - `FakeDriver` additions: `set_ocr_boxes(boxes: list[RawTextBox])` (returned by every `ocr()` call), `ocr_calls: int` counter, `draw_rect(region_pt: Region, color: tuple[int, int, int])` (paints the virtual screen so diff-based tests can change it), and `ocr` participates in `fail_next`.
  - Config: `HandsConfig.ocr: OCRConfig(languages: list[str] = ["en-US"], cache_size: int = 20)`; `HandsConfig.waiter: WaiterConfig(poll_start_ms: int = 100, poll_max_ms: int = 500, stable_threshold: float = 0.01)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_fake_driver.py`:

```python
from hands.driver.base import RawTextBox


def test_fake_ocr_returns_scripted_boxes():
    drv = FakeDriver()
    boxes = [RawTextBox("Submit", 0.1, 0.2, 0.3, 0.05, 0.99)]
    drv.set_ocr_boxes(boxes)
    frame = drv.capture(None, None)
    assert drv.ocr(frame, ["en-US"]) == boxes
    assert drv.ocr_calls == 1


def test_fake_ocr_fail_injection():
    drv = FakeDriver()
    drv.fail_next("ocr", DriverError("vision unavailable"))
    with pytest.raises(DriverError):
        drv.ocr(drv.capture(None, None), ["en-US"])


def test_draw_rect_changes_captured_pixels():
    drv = FakeDriver()
    before = drv.capture(None, None).image.copy()
    drv.draw_rect(Region(0, 0, 100, 100), (255, 0, 0))
    after = drv.capture(None, None).image
    assert before.getpixel((10, 10)) != after.getpixel((10, 10))
```

Append to `tests/unit/test_config.py`:

```python
def test_m2_config_sections():
    cfg = HandsConfig()
    assert cfg.ocr.languages == ["en-US"]
    assert cfg.ocr.cache_size == 20
    assert cfg.waiter.poll_start_ms == 100
    assert cfg.waiter.poll_max_ms == 500
    assert cfg.waiter.stable_threshold == 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_config.py -q`
Expected: FAIL — `ImportError: RawTextBox` / `AttributeError: ocr`.

- [ ] **Step 3: Implement**

Append to `src/hands/driver/base.py` (and add `ocr` to the `Driver` protocol body):

```python
@dataclass(frozen=True, slots=True)
class RawTextBox:
    """OCR box exactly as Vision reports it: normalized [0,1], BOTTOM-LEFT
    origin, relative to the recognized frame. Services convert to canonical
    points via CoordinateMapper.vision_normalized_to_pt (DESIGN §4.10)."""
    text: str
    nx: float
    ny: float
    nw: float
    nh: float
    confidence: float
```

Inside `class Driver(Protocol)` add:

```python
    def ocr(self, frame: RawFrame,
            languages: list[str]) -> list[RawTextBox]: ...
```

Re-export in `src/hands/driver/__init__.py`: add `RawTextBox` to the import and `__all__`.

In `src/hands/driver/fake.py`, add to `__init__`:

```python
        self._ocr_boxes: list = []
        self.ocr_calls = 0
```

and the methods:

```python
    def set_ocr_boxes(self, boxes: list) -> None:
        self._ocr_boxes = list(boxes)

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

    def ocr(self, frame: RawFrame, languages: list[str]) -> list:
        self._maybe_fail("ocr")
        self.ocr_calls += 1
        return list(self._ocr_boxes)
```

In `src/hands/config.py` add (mirroring the existing nested-model style):

```python
class OCRConfig(BaseModel):
    languages: list[str] = ["en-US"]
    cache_size: int = 20


class WaiterConfig(BaseModel):
    poll_start_ms: int = 100
    poll_max_ms: int = 500
    stable_threshold: float = 0.01
```

and the two fields on `HandsConfig`:

```python
    ocr: OCRConfig = OCRConfig()
    waiter: WaiterConfig = WaiterConfig()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fake_driver.py tests/unit/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 4: OCR service (`services/ocr.py`)

**Files:**
- Create: `src/hands/services/ocr.py`
- Test: `tests/unit/test_ocr_service.py`

**Interfaces:**
- Consumes: `Driver.ocr`, `RawTextBox` (Task 3), `CoordinateMapper.vision_normalized_to_pt` (Task 2), `perceptual_hash` (M1 Task 7), `TextBox` (Task 2), `HandsConfig.ocr` (Task 3).
- Produces: `OCRService(driver, coords, config)` with `async recognize(region: Region | None = None, languages: list[str] | None = None) -> list[TextBox]`. Results are cached per frame content (perceptual hash + bounds); cache is LRU-bounded by `config.ocr.cache_size`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_ocr_service.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    coords = CoordinateMapper(fake_driver.displays())
    return OCRService(fake_driver, coords, HandsConfig())


async def test_recognize_converts_to_canonical_points(fake_driver, service):
    # Box occupying the bottom-left quarter of the 1440x900 screen.
    fake_driver.set_ocr_boxes(
        [RawTextBox("Login", 0.0, 0.0, 0.5, 0.5, 0.9)])
    (box,) = await service.recognize()
    assert box.text == "Login"
    assert box.confidence == 0.9
    # bottom-left quarter in y-down points = top edge at 450.
    assert (box.region.x, box.region.y) == (0, 450)
    assert (box.region.width, box.region.height) == (720, 450)


async def test_recognize_region_is_frame_relative(fake_driver, service):
    fake_driver.set_ocr_boxes(
        [RawTextBox("OK", 0.0, 0.0, 1.0, 1.0, 1.0)])
    (box,) = await service.recognize(region=Region(100, 50, 200, 100))
    assert (box.region.x, box.region.y,
            box.region.width, box.region.height) == (100, 50, 200, 100)


async def test_identical_frame_is_cached(fake_driver, service):
    fake_driver.set_ocr_boxes([RawTextBox("A", 0, 0, 0.1, 0.1, 1.0)])
    await service.recognize()
    await service.recognize()
    assert fake_driver.ocr_calls == 1


async def test_changed_frame_busts_cache(fake_driver, service):
    fake_driver.set_ocr_boxes([RawTextBox("A", 0, 0, 0.1, 0.1, 1.0)])
    await service.recognize()
    fake_driver.draw_rect(Region(0, 0, 400, 400), (200, 10, 10))
    await service.recognize()
    assert fake_driver.ocr_calls == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_ocr_service.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.ocr`.

- [ ] **Step 3: Implement `src/hands/services/ocr.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ocr_service.py -q`
Expected: 4 passed.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 5: `find_text` tool

**Files:**
- Modify: `src/hands/tools/observe.py` (append args model + handler + registration)
- Modify: `src/hands/container.py` (wire `OCRService`)
- Test: `tests/unit/test_tools_observe_m2.py`

**Interfaces:**
- Consumes: `OCRService` (Task 4), `RegionArg` (already in M1 `tools/observe.py`), registry/dispatcher (M1 Task 13), `Container` (M1 Task 15).
- Produces:
  - `Container.ocr: OCRService` — built right after `self.screenshots` in `Container.build`: `self.ocr = OCRService(self.driver, self.coords, config)`.
  - Tool `find_text` — args `{text: str (1..200), region?: RegionArg, fuzzy: bool = true}`; response `{ok, matches: [{text, region: {x,y,width,height}, center: {x,y}, confidence}]}` sorted by confidence descending. Fuzzy matching = case-insensitive substring either way, or `difflib.SequenceMatcher` ratio ≥ 0.8; exact matching = case-sensitive substring.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_tools_observe_m2.py`:

```python
from types import SimpleNamespace

import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.driver.fake import FakeDriver
from hands.registry import ToolRegistry
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.state import StateManager
from hands.tools import register_builtin_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def env():
    cfg = HandsConfig()
    driver = FakeDriver()
    state = StateManager(cfg)
    coords = CoordinateMapper(driver.displays())
    shots = ScreenshotService(driver, state, cfg)
    ocr = OCRService(driver, coords, cfg)
    container = SimpleNamespace(config=cfg, driver=driver, state=state,
                                coords=coords, screenshots=shots, ocr=ocr,
                                mouse=None, keyboard=None)
    reg = ToolRegistry()
    register_builtin_tools(reg, container)
    return SimpleNamespace(driver=driver, registry=reg)


async def _call(env, name, args):
    spec = env.registry.get(name)
    return await spec.handler(spec.args_model.model_validate(args), None)


async def test_find_text_returns_clickable_center(env):
    env.driver.set_ocr_boxes(
        [RawTextBox("Submit", 0.0, 0.0, 0.5, 0.5, 0.97)])
    res = await _call(env, "find_text", {"text": "submit"})
    (m,) = res["matches"]
    assert m["text"] == "Submit"
    assert m["center"] == {"x": 360.0, "y": 675.0}
    assert m["confidence"] == 0.97


async def test_find_text_fuzzy_and_exact(env):
    env.driver.set_ocr_boxes(
        [RawTextBox("Submlt", 0.0, 0.0, 0.5, 0.5, 0.8)])  # OCR typo
    fuzzy = await _call(env, "find_text", {"text": "Submit"})
    assert len(fuzzy["matches"]) == 1
    exact = await _call(env, "find_text",
                        {"text": "Submit", "fuzzy": False})
    assert exact["matches"] == []


async def test_find_text_no_match_is_ok_empty(env):
    env.driver.set_ocr_boxes([])
    res = await _call(env, "find_text", {"text": "anything"})
    assert res["matches"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_observe_m2.py -q`
Expected: FAIL — `InvalidArgsError: unknown tool: find_text` (raised via `registry.get`).

- [ ] **Step 3: Implement**

In `src/hands/container.py`, after the `self.screenshots = ...` line, add:

```python
        self.ocr = OCRService(self.driver, self.coords, config)
```

(with `from .services.ocr import OCRService` in the imports).

**Ripple:** `observe.register` now reads `container.ocr`, so any M1 test that builds a `SimpleNamespace` container for `register_builtin_tools` (M1 Task 14's `tests/unit/test_tools.py`) must gain an `ocr=OCRService(driver, CoordinateMapper(driver.displays()), cfg)` attribute.

Append to `src/hands/tools/observe.py`:

```python
class FindTextArgs(BaseModel, extra="forbid"):
    text: str = Field(min_length=1, max_length=200)
    region: RegionArg | None = None
    fuzzy: bool = True
```

Inside `register(registry, container)` add (before the `registry.register` block at the end):

```python
    ocr = container.ocr

    def _text_matches(query: str, candidate: str, fuzzy: bool) -> bool:
        if not fuzzy:
            return query in candidate
        q, c = query.lower(), candidate.lower()
        if q in c or c in q:
            return True
        import difflib
        return difflib.SequenceMatcher(None, q, c).ratio() >= 0.8

    async def find_text(args: FindTextArgs, ctx) -> dict:
        region = (Region(**args.region.model_dump())
                  if args.region else None)
        boxes = await ocr.recognize(region)
        matches = [
            {"text": b.text,
             "region": {"x": b.region.x, "y": b.region.y,
                        "width": b.region.width,
                        "height": b.region.height},
             "center": {"x": b.region.center.x, "y": b.region.center.y},
             "confidence": b.confidence}
            for b in boxes if _text_matches(args.text, b.text, args.fuzzy)
        ]
        matches.sort(key=lambda m: -m["confidence"])
        return {"matches": matches}

    registry.register(ToolSpec(
        "find_text",
        "OCR the screen (or a region, in points) and return boxes matching "
        "`text`. Each match has a `center` you can pass directly to "
        "mouse_click. Re-observe rather than act when confidence < 0.5.",
        FindTextArgs, find_text, "read", RetryPolicy.read(),
        idempotent=True))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_observe_m2.py -q`
Expected: 3 passed.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: all pass — including M1's `test_server_e2e.py`, which now lists 10 tools; if an M1 test asserts the exact tool count/list, update it to include `find_text`.

---

### Task 6: Waiter and condition-based `wait`

**Files:**
- Create: `src/hands/services/waiter.py`
- Modify: `src/hands/tools/observe.py` (replace M1's `WaitArgs` + `wait` handler)
- Modify: `src/hands/container.py` (wire `Waiter`)
- Test: `tests/unit/test_waiter.py`, `tests/unit/test_tools_observe_m2.py` (append)

**Interfaces:**
- Consumes: `ScreenshotService` (M1 Task 10), `OCRService` (Task 4), `HandsConfig.waiter` (Task 3), `InvalidArgsError` (M1 Task 2).
- Produces:
  - `WaitResult(met: bool, waited_ms: int, evidence: dict)` frozen dataclass.
  - `Waiter(screenshots, ocr, config)` with `async wait_for(cond: dict, timeout_ms: int) -> WaitResult`. M2 condition types: `duration {ms}`, `text_present {text, region?}`, `screen_stable {quiet_ms}`. Unknown type → `InvalidArgsError` listing known types. Polling starts at `poll_start_ms`, multiplies by 1.5 up to `poll_max_ms`. **M3 extends the checker table** with `window_present`/`window_gone`/`app_frontmost` — build it as a dict so extension is one entry.
  - `Container.waiter: Waiter` built after `self.ocr`.
  - Upgraded `wait` tool: args `{condition?: dict, duration_ms?: int, timeout_ms: int = 10000}` — exactly one of `condition`/`duration_ms` required (`duration_ms` is M1 back-compat sugar for `{"type": "duration", "ms": n}`). Response `{ok, met, waited_ms, evidence}`. Timeout is `met: false`, not an error (DESIGN §5.14).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_waiter.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.waiter import Waiter
from hands.state import StateManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def waiter(fake_driver):
    cfg = HandsConfig()
    cfg.waiter.poll_start_ms = 5
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    return Waiter(shots, ocr, cfg)


async def test_duration(waiter):
    res = await waiter.wait_for({"type": "duration", "ms": 10}, 1000)
    assert res.met and res.waited_ms == 10


async def test_text_present_met(fake_driver, waiter):
    fake_driver.set_ocr_boxes([RawTextBox("Done", 0, 0, 0.2, 0.1, 0.9)])
    res = await waiter.wait_for(
        {"type": "text_present", "text": "done"}, 500)
    assert res.met
    assert res.evidence["matches"][0]["text"] == "Done"


async def test_text_present_timeout_is_answer_not_error(waiter):
    res = await waiter.wait_for(
        {"type": "text_present", "text": "never"}, 60)
    assert res.met is False
    assert res.waited_ms >= 60


async def test_screen_stable_on_static_screen(waiter):
    res = await waiter.wait_for(
        {"type": "screen_stable", "quiet_ms": 20}, 2000)
    assert res.met


async def test_unknown_condition(waiter):
    with pytest.raises(InvalidArgsError):
        await waiter.wait_for({"type": "moon_phase"}, 100)
```

Append to `tests/unit/test_tools_observe_m2.py` (the `env` fixture needs `waiter=Waiter(shots, ocr, cfg)` added to the `SimpleNamespace` — add it now):

```python
async def test_wait_tool_condition(env):
    env.driver.set_ocr_boxes([RawTextBox("Ready", 0, 0, 0.2, 0.1, 1.0)])
    res = await _call(env, "wait", {
        "condition": {"type": "text_present", "text": "Ready"},
        "timeout_ms": 500})
    assert res["met"] is True


async def test_wait_tool_duration_back_compat(env):
    res = await _call(env, "wait", {"duration_ms": 5})
    assert res["met"] is True


async def test_wait_tool_requires_exactly_one_form(env):
    from pydantic import ValidationError
    spec = env.registry.get("wait")
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({})
    with pytest.raises(ValidationError):
        spec.args_model.model_validate(
            {"duration_ms": 5, "condition": {"type": "duration", "ms": 1}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_waiter.py tests/unit/test_tools_observe_m2.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.waiter`.

- [ ] **Step 3: Implement `src/hands/services/waiter.py`**

```python
"""Poll-based wait-for-condition engine (DESIGN §4.14)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import anyio

from ..config import HandsConfig
from ..errors import InvalidArgsError
from .ocr import OCRService
from .screenshot import ScreenshotService


@dataclass(frozen=True, slots=True)
class WaitResult:
    met: bool
    waited_ms: int
    evidence: dict


class Waiter:
    def __init__(self, screenshots: ScreenshotService, ocr: OCRService,
                 config: HandsConfig) -> None:
        self._shots = screenshots
        self._ocr = ocr
        self._cfg = config.waiter
        # M3 adds window_present / window_gone / app_frontmost here.
        self._checkers = {
            "text_present": self._text_present,
            "screen_stable": self._screen_stable,
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
        query = str(cond.get("text", "")).lower()
        matches = [b for b in boxes if query in b.text.lower()]
        evidence = {"matches": [
            {"text": b.text, "confidence": b.confidence,
             "center": {"x": b.region.center.x, "y": b.region.center.y}}
            for b in matches]}
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
```

- [ ] **Step 4: Replace the `wait` tool** in `src/hands/tools/observe.py`.

Replace M1's `WaitArgs` and `wait` handler with:

```python
class WaitArgs(BaseModel, extra="forbid"):
    condition: dict | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=60_000)
    timeout_ms: int = Field(default=10_000, ge=0, le=120_000)

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.condition is None) == (self.duration_ms is None):
            raise ValueError(
                "provide exactly one of `condition` or `duration_ms`")
        return self
```

(add `model_validator` to the pydantic import). New handler + registration (the `register` function needs `waiter = container.waiter` at the top):

```python
    async def wait(args: WaitArgs, ctx) -> dict:
        cond = args.condition or {"type": "duration",
                                  "ms": args.duration_ms}
        res = await waiter.wait_for(cond, args.timeout_ms)
        return {"met": res.met, "waited_ms": res.waited_ms,
                "evidence": res.evidence}

    registry.register(ToolSpec(
        "wait",
        "Wait for a condition: {type: 'duration', ms} | {type: "
        "'text_present', text, region?} | {type: 'screen_stable', "
        "quiet_ms}. Timeout returns met=false (an answer, not an error).",
        WaitArgs, wait, "read", RetryPolicy.none(), idempotent=True))
```

In `src/hands/container.py`, after `self.ocr = ...`:

```python
        self.waiter = Waiter(self.screenshots, self.ocr, config)
```

(import `Waiter` from `.services.waiter`). Also add `waiter` to the `SimpleNamespace` in `tests/unit/test_tools_observe_m2.py`'s `env` fixture and in M1's `tests/unit/test_tools.py` container stub (it constructs the same `SimpleNamespace`; `wait` now needs `container.waiter`). M1's dispatcher test `dispatch("wait", {"duration_ms": 10})` keeps passing unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_waiter.py tests/unit/test_tools_observe_m2.py -q`
Expected: all pass.

- [ ] **Step 6: Verify**

Run: `uv run pytest -q`
Expected: all pass.

---

### Task 7: Verification engine and `verify` tool

**Files:**
- Create: `src/hands/services/verification.py`
- Modify: `src/hands/tools/observe.py` (add `verify`)
- Modify: `src/hands/container.py` (wire `VerificationEngine`)
- Test: `tests/unit/test_verification.py`, `tests/unit/test_tools_observe_m2.py` (append)

**Interfaces:**
- Consumes: `ScreenshotService`/`Screenshot` (M1 Task 10), `OCRService` (Task 4), `Driver.cursor_position` (M1 Task 6), `frame_diff`/`crop` (Task 1), errors (M1 Task 2).
- Produces:
  - `Expectation(type: str, params: dict, children: tuple[Expectation, ...])` frozen dataclass with classmethod `from_wire(raw: dict) -> Expectation` — `raw` is `{"type": ..., "children": [...], **params}`; unknown type → `InvalidArgsError`.
  - `VerificationResult(passed: bool, confidence: float, evidence: dict, failed_clauses: tuple[str, ...] = ())` frozen dataclass.
  - `VerificationEngine(screenshots, ocr, driver, config)` with `async verify(expect: Expectation, baseline: Screenshot | None = None) -> VerificationResult`. M2 strategies: `text_present`, `text_absent`, `region_changed`, `region_unchanged`, `cursor_at`, `all_of`, `any_of`. **M3 adds** `window_present`, `window_gone`, `clipboard_contains` to the same table.
  - Confidence rules: OCR strategies carry the best matching box's confidence (`text_absent` passes with `1 - best_match_confidence`, 1.0 when nothing matched); diff strategies use `min(1.0, changed_fraction / threshold)` for `region_changed` and its complement for `region_unchanged`; `cursor_at` is binary; composites take `min` (`all_of`) / `max` (`any_of`) of children.
  - `Container.verification: VerificationEngine` built after `self.waiter`.
  - Tool `verify` — args `{expect: dict, baseline_screenshot_id?: str}`; response `{ok, passed, confidence, evidence, failed_clauses}`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_verification.py`:

```python
import pytest

from hands.config import HandsConfig
from hands.driver.base import RawTextBox
from hands.errors import InvalidArgsError
from hands.services.coords import CoordinateMapper
from hands.services.ocr import OCRService
from hands.services.screenshot import ScreenshotService
from hands.services.verification import (
    Expectation,
    VerificationEngine,
)
from hands.state import StateManager
from hands.types import Region

pytestmark = pytest.mark.anyio


@pytest.fixture
def env(fake_driver):
    cfg = HandsConfig()
    state = StateManager(cfg)
    shots = ScreenshotService(fake_driver, state, cfg)
    ocr = OCRService(fake_driver, CoordinateMapper(fake_driver.displays()),
                     cfg)
    engine = VerificationEngine(shots, ocr, fake_driver, cfg)
    return fake_driver, shots, engine


def test_from_wire_parses_composites():
    e = Expectation.from_wire({
        "type": "all_of",
        "children": [{"type": "text_present", "text": "OK"},
                     {"type": "cursor_at", "x": 1, "y": 2}]})
    assert e.type == "all_of"
    assert e.children[0].params == {"text": "OK"}


def test_from_wire_rejects_unknown_type():
    with pytest.raises(InvalidArgsError):
        Expectation.from_wire({"type": "vibes"})


async def test_text_present_and_absent(env):
    driver, _, engine = env
    driver.set_ocr_boxes([RawTextBox("Saved", 0, 0, 0.2, 0.1, 0.9)])
    ok = await engine.verify(
        Expectation.from_wire({"type": "text_present", "text": "Saved"}))
    assert ok.passed and ok.confidence == 0.9
    absent = await engine.verify(
        Expectation.from_wire({"type": "text_absent", "text": "Saved"}))
    assert not absent.passed


async def test_region_changed_against_baseline(env):
    driver, shots, engine = env
    baseline = await shots.capture(fresh=True)
    driver.draw_rect(Region(0, 0, 200, 200), (255, 255, 255))
    changed = await engine.verify(
        Expectation.from_wire({"type": "region_changed",
                               "region": {"x": 0, "y": 0,
                                          "width": 200, "height": 200}}),
        baseline=baseline)
    assert changed.passed
    unchanged = await engine.verify(
        Expectation.from_wire({"type": "region_unchanged",
                               "region": {"x": 1200, "y": 700,
                                          "width": 100, "height": 100}}),
        baseline=baseline)
    assert unchanged.passed


async def test_region_changed_requires_baseline(env):
    _, _, engine = env
    with pytest.raises(InvalidArgsError):
        await engine.verify(Expectation.from_wire(
            {"type": "region_changed",
             "region": {"x": 0, "y": 0, "width": 10, "height": 10}}))


async def test_cursor_at_with_tolerance(env):
    driver, _, engine = env
    from hands.driver.base import MouseEventSpec
    from hands.types import MouseButton, Point
    driver.post_mouse(MouseEventSpec("move", Point(100, 100),
                                     MouseButton.LEFT))
    res = await engine.verify(Expectation.from_wire(
        {"type": "cursor_at", "x": 101, "y": 99}))
    assert res.passed and res.confidence == 1.0


async def test_all_of_collects_failed_clauses(env):
    driver, _, engine = env
    driver.set_ocr_boxes([RawTextBox("Saved", 0, 0, 0.2, 0.1, 0.9)])
    res = await engine.verify(Expectation.from_wire({
        "type": "all_of",
        "children": [{"type": "text_present", "text": "Saved"},
                     {"type": "text_present", "text": "Missing"}]}))
    assert not res.passed
    assert "text_present" in res.failed_clauses
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_verification.py -q`
Expected: FAIL — `ModuleNotFoundError: hands.services.verification`.

- [ ] **Step 3: Implement `src/hands/services/verification.py`**

```python
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
from .ocr import OCRService
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
    DIFF_THRESHOLD = 0.01   # changed_fraction above this = "changed"

    def __init__(self, screenshots: ScreenshotService, ocr: OCRService,
                 driver: Driver, config: HandsConfig) -> None:
        self._shots = screenshots
        self._ocr = ocr
        self._driver = driver
        self._cfg = config

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
        query = str(params.get("text", "")).lower()
        matches = [b for b in boxes if query in b.text.lower()]
        best = max((b.confidence for b in matches), default=0.0)
        evidence = {"matches": [
            {"text": b.text, "confidence": b.confidence,
             "center": {"x": b.region.center.x, "y": b.region.center.y}}
            for b in matches],
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
        confidence = min(1.0, diff.changed_fraction / self.DIFF_THRESHOLD)
        return VerificationResult(
            diff.changed_fraction > self.DIFF_THRESHOLD, confidence,
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
```

- [ ] **Step 4: Wire the container and the `verify` tool**

In `src/hands/container.py`, after `self.waiter = ...`:

```python
        self.verification = VerificationEngine(
            self.screenshots, self.ocr, self.driver, config)
```

Append to `src/hands/tools/observe.py` (the `register` function needs `verification = container.verification`):

```python
class VerifyArgs(BaseModel, extra="forbid"):
    expect: dict
    baseline_screenshot_id: str | None = None
```

```python
    async def verify(args: VerifyArgs, ctx) -> dict:
        expectation = Expectation.from_wire(args.expect)
        baseline = (shots.get(args.baseline_screenshot_id)
                    if args.baseline_screenshot_id else None)
        res = await verification.verify(expectation, baseline)
        return {"passed": res.passed, "confidence": res.confidence,
                "evidence": res.evidence,
                "failed_clauses": list(res.failed_clauses)}

    registry.register(ToolSpec(
        "verify",
        "Check an expected outcome after acting. expect = {type: "
        "'text_present'|'text_absent'|'region_changed'|'region_unchanged'"
        "|'cursor_at'|'all_of'|'any_of', ...params, children?}. "
        "region_changed/unchanged need baseline_screenshot_id from an "
        "earlier screenshot response.",
        VerifyArgs, verify, "read", RetryPolicy.read(), idempotent=True))
```

(import `Expectation` from `..services.verification` at the top of `observe.py`.)

Append to `tests/unit/test_tools_observe_m2.py` (the `env` fixture also gains `verification=VerificationEngine(shots, ocr, driver, cfg)`):

```python
async def test_verify_tool_text_present(env):
    env.driver.set_ocr_boxes([RawTextBox("Done", 0, 0, 0.2, 0.1, 0.95)])
    res = await _call(env, "verify",
                      {"expect": {"type": "text_present", "text": "Done"}})
    assert res["passed"] is True
    assert res["confidence"] == 0.95
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_verification.py tests/unit/test_tools_observe_m2.py -q`
Expected: all pass.

- [ ] **Step 6: Verify**

Run: `uv run pytest -q`
Expected: all pass. Tool count is now 11 (`find_text`, `verify` added; `wait` upgraded, not a new tool). Update any M1 test asserting the tool list.

---

### Task 8: macOS Vision OCR and ScreenCaptureKit capture

**Files:**
- Modify: `pyproject.toml` (macos extra)
- Modify: `src/hands/driver/macos.py`
- Test: `tests/contract/test_driver_contract.py` (append parametrized cases), `tests/contract/test_macos_m2.py`

**Interfaces:**
- Consumes: `RawFrame`, `RawTextBox` (Task 3), M1 `MacOSDriver` internals (`_capture_cli`, display logic).
- Produces: `MacOSDriver.ocr(frame, languages) -> list[RawTextBox]` via `VNRecognizeTextRequest` (accurate mode); `MacOSDriver.capture` tries ScreenCaptureKit (`SCScreenshotManager`) first and falls back to the M1 `screencapture` CLI path on any error or a 2 s timeout.

- [ ] **Step 1: Add macOS dependencies** — in `pyproject.toml` extend the `macos` extra:

```toml
[project.optional-dependencies]
macos = [
    "pyobjc-framework-Quartz>=10.2; sys_platform == 'darwin'",
    "pyobjc-framework-Vision>=10.2; sys_platform == 'darwin'",
    "pyobjc-framework-ScreenCaptureKit>=10.2; sys_platform == 'darwin'",
]
```

Run: `uv sync --extra macos` (on macOS). Expected: resolves and installs.

- [ ] **Step 2: Write the failing contract tests** — `tests/contract/test_macos_m2.py`:

```python
"""Real-driver perception contract. Gated: HANDS_CONTRACT_MACOS=1."""
import os
import sys

import pytest
from PIL import Image, ImageDraw

from hands.driver.base import RawFrame
from hands.types import Region

pytestmark = pytest.mark.skipif(
    os.environ.get("HANDS_CONTRACT_MACOS") != "1"
    or sys.platform != "darwin",
    reason="real macOS driver contract tests are opt-in")


@pytest.fixture
def driver():
    from hands.driver.macos import MacOSDriver
    return MacOSDriver()


def test_ocr_reads_rendered_text(driver):
    img = Image.new("RGB", (800, 200), (255, 255, 255))
    ImageDraw.Draw(img).text((40, 60), "HELLO HANDS", fill=(0, 0, 0),
                             font_size=64)
    frame = RawFrame(img, Region(0, 0, 400, 100), 2.0, 1)
    boxes = driver.ocr(frame, ["en-US"])
    assert any("HELLO" in b.text.upper() for b in boxes)
    for b in boxes:
        assert 0.0 <= b.nx <= 1.0 and 0.0 <= b.ny <= 1.0
        assert 0.0 < b.confidence <= 1.0


def test_capture_still_returns_sane_frame(driver):
    frame = driver.capture(None, None)
    assert frame.px_per_pt >= 1.0
    assert frame.image.size[0] > 0
```

- [ ] **Step 3: Run tests to verify they fail** (on macOS)

Run: `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract/test_macos_m2.py -q`
Expected: FAIL — `AttributeError: 'MacOSDriver' object has no attribute 'ocr'`.

- [ ] **Step 4: Implement** — add to `src/hands/driver/macos.py`:

```python
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
```

(import `RawTextBox` alongside `RawFrame` at the top of `macos.py`.)

Then make `capture` try ScreenCaptureKit first. Rename the M1 body to `_capture_cli` if M1 didn't already, and add:

```python
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
```

Notes for the implementer:
- `_display_scale(display_id)` and `_display_info(display_id)` are tiny lookups over the M1 `displays()` result — add them as private helpers if M1 didn't already have equivalents (`next(d for d in self.displays() if d.display_id == display_id)`).
- `log` is the module-level `structlog.get_logger(__name__)`; add it if `macos.py` doesn't have one.
- On macOS 14+, SCK screenshots need the same Screen Recording TCC grant as `screencapture`; the fallback keeps `capture` working while permissions are being sorted.

- [ ] **Step 5: Run tests to verify they pass** (on macOS with Screen Recording granted)

Run: `HANDS_CONTRACT_MACOS=1 uv run pytest tests/contract -q`
Expected: all pass, including M1's contract suite (capture semantics unchanged).

- [ ] **Step 6: Verify**

Run: `uv run pytest -q` (any OS — contract tests skip) and, on macOS, `HANDS_CONTRACT_MACOS=1 uv run pytest -q`.
Expected: all pass.

---

## Plan completion criteria

- `uv run pytest -q` green on any OS (fake driver end to end).
- On macOS with permissions: `HANDS_CONTRACT_MACOS=1 uv run pytest -q` green — Vision OCR reads rendered text; SCK capture works with CLI fallback.
- An MCP client over stdio can: `find_text` a visible string and click its `center`; `wait` on `{"type": "screen_stable", "quiet_ms": 500}`; `verify` a `text_present` expectation and a `region_changed` against a baseline screenshot id.
- 11 tools registered: M1's 9 (with `wait` upgraded, not a new tool) + `find_text` + `verify`.
- Nothing committed to git (user instruction).
