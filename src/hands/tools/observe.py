"""Observation tools: screenshot, get_state, wait, find_text, verify
(condition-based, M2)."""
from __future__ import annotations

import base64
import dataclasses
import difflib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy
from ..services.verification import Expectation
from ..types import Region


class RegionArg(BaseModel, extra="forbid"):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ScreenshotArgs(BaseModel, extra="forbid"):
    region: RegionArg | None = None
    format: Literal["png", "jpeg"] = "png"
    max_dim: int | None = Field(default=None, ge=64, le=4096)
    fresh: bool = False


class GetStateArgs(BaseModel, extra="forbid"):
    include_history: int = Field(default=0, ge=0, le=50)


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


class FindTextArgs(BaseModel, extra="forbid"):
    text: str = Field(min_length=1, max_length=200)
    region: RegionArg | None = None
    fuzzy: bool = True


class VerifyArgs(BaseModel, extra="forbid"):
    expect: dict
    baseline_screenshot_id: str | None = None


def register(registry: ToolRegistry, container) -> None:
    shots = container.screenshots
    state = container.state
    driver = container.driver
    config = container.config
    waiter = container.waiter
    verification = container.verification
    ocr = container.ocr

    async def screenshot(args: ScreenshotArgs, ctx) -> dict:
        region = (Region(**args.region.model_dump())
                  if args.region else None)
        shot = await shots.capture(region, fmt=args.format,
                                   max_dim=args.max_dim, fresh=args.fresh)
        return {"image_b64": base64.b64encode(shot.data).decode(),
                **shot.meta()}

    async def get_state(args: GetStateArgs, ctx) -> dict:
        cur = driver.cursor_position()
        return {
            "cursor": {"x": cur.x, "y": cur.y},
            "displays": [dataclasses.asdict(d) for d in driver.displays()],
            "latest_screenshot": state.latest_screenshot_meta,
            "screen_dirty": state.screen_dirty,
            "kill_switch": config.security.kill_switch_engaged(),
            "history": [dataclasses.asdict(r)
                        for r in state.history(args.include_history)]
            if args.include_history else [],
        }

    async def wait(args: WaitArgs, ctx) -> dict:
        cond = args.condition or {"type": "duration",
                                  "ms": args.duration_ms}
        res = await waiter.wait_for(cond, args.timeout_ms)
        return {"met": res.met, "waited_ms": res.waited_ms,
                "evidence": res.evidence}

    def _text_matches(query: str, candidate: str, fuzzy: bool) -> bool:
        if not fuzzy:
            return query in candidate
        q, c = query.lower(), candidate.lower()
        if q in c or c in q:
            return True
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

    async def verify(args: VerifyArgs, ctx) -> dict:
        expectation = Expectation.from_wire(args.expect)
        baseline = (shots.get(args.baseline_screenshot_id)
                    if args.baseline_screenshot_id else None)
        res = await verification.verify(expectation, baseline)
        return {"passed": res.passed, "confidence": res.confidence,
                "evidence": res.evidence,
                "failed_clauses": list(res.failed_clauses)}

    registry.register(ToolSpec(
        "screenshot",
        "Capture the screen (or a region, in points). The response includes "
        "bounds_pt and px_per_pt: point = bounds_pt.origin + pixel / "
        "px_per_pt. Take a screenshot before any coordinate action.",
        ScreenshotArgs, screenshot, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "get_state",
        "Re-orientation: cursor position, displays, last screenshot "
        "metadata, kill-switch status, and recent action history.",
        GetStateArgs, get_state, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "wait",
        "Wait for a condition: {type: 'duration', ms} | {type: "
        "'text_present', text, region?} | {type: 'screen_stable', "
        "quiet_ms}. Timeout returns met=false (an answer, not an error). "
        "`text_present` requires an exact (case-insensitive) substring "
        "match, not fuzzy matching.",
        WaitArgs, wait, "read", RetryPolicy.none(), idempotent=True))
    registry.register(ToolSpec(
        "find_text",
        "OCR the screen (or a region, in points) and return boxes matching "
        "`text`. Each match has a `center` you can pass directly to "
        "mouse_click. Re-observe rather than act when confidence < 0.5. "
        "By default this matches fuzzily (case-insensitive substring "
        "either direction, or a similarity ratio >= 0.8); pass "
        "fuzzy=false for exact substring matching. This differs from "
        "`wait`'s `text_present` condition and `verify`'s `text_present` "
        "expectation, which always require an exact substring match.",
        FindTextArgs, find_text, "read", RetryPolicy.read(),
        idempotent=True))
    registry.register(ToolSpec(
        "verify",
        "Check an expected outcome after acting. expect = {type: "
        "'text_present'|'text_absent'|'region_changed'|'region_unchanged'"
        "|'cursor_at'|'all_of'|'any_of', ...params, children?}. "
        "region_changed/unchanged need baseline_screenshot_id from an "
        "earlier screenshot response. `text_present`/`text_absent` "
        "require an exact (case-insensitive) substring match, not fuzzy.",
        VerifyArgs, verify, "read", RetryPolicy.read(), idempotent=True))
