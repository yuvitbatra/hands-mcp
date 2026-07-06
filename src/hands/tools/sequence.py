"""execute_sequence: latency batching of pre-decided actions with guard
conditions (DESIGN §5.16). A macro, not a planner."""
from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from ..errors import InvalidArgsError, PolicyDeniedError
from ..permissions import ActionDescriptor, Denied, NeedsConfirmation
from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy


class StepArg(BaseModel, extra="forbid"):
    tool: str
    args: dict = {}
    guard: dict | None = None
    guard_timeout_ms: int = Field(default=5_000, ge=0, le=60_000)


class SequenceArgs(BaseModel, extra="forbid"):
    steps: list[StepArg] = Field(min_length=1, max_length=20)
    stop_on_failure: bool = True
    screenshot_after: bool = True


def register(registry: ToolRegistry, container) -> None:
    async def execute_sequence(args: SequenceArgs, ctx) -> dict:
        dispatcher = container.dispatcher
        permissions = dispatcher.permissions  # same engine as the outer dispatch
        waiter = container.waiter
        shots = container.screenshots

        # -- validate every step up front (DESIGN §5.16) ------------------
        specs = []
        for i, step in enumerate(args.steps):
            if step.tool == "execute_sequence":
                raise InvalidArgsError(
                    f"step {i}: nested sequences are not allowed")
            spec = registry.get(step.tool)      # unknown -> INVALID_ARGS
            if spec.policy_class == "read":
                raise InvalidArgsError(
                    f"step {i}: read tool {step.tool!r} not allowed in a "
                    f"sequence — call it directly")
            specs.append(spec)

        # -- authorize the whole sequence and each step --------------------
        target_app = dispatcher.frontmost_app()
        for i, (step, spec) in enumerate(zip(args.steps, specs)):
            try:
                validated = spec.args_model.model_validate(step.args)
            except Exception as e:
                raise InvalidArgsError(f"step {i} ({step.tool}): {e}")
            policy_class = spec.policy_class
            if spec.escalate is not None and spec.escalate(validated):
                policy_class = "sensitive"
            action = ActionDescriptor(
                step.tool, policy_class, target_app=target_app,
                text=getattr(validated, "text", None))
            decision = permissions.authorize(action)
            if isinstance(decision, NeedsConfirmation):
                if not await permissions.confirm(decision.prompt, action):
                    decision = Denied(f"user declined step {i}: "
                                      f"{step.tool}")
            if isinstance(decision, Denied):
                raise PolicyDeniedError(
                    f"sequence rejected: {decision.reason}",
                    details={"step": i, "tool": step.tool})

        # -- execute -------------------------------------------------------
        results: list[dict] = []
        completed = 0
        halted_by: str | None = None
        guard_evidence: dict | None = None
        for step in args.steps:
            if halted_by is not None:
                results.append({"skipped": True})
                continue
            if step.guard is not None:
                wait = await waiter.wait_for(step.guard,
                                             step.guard_timeout_ms)
                if not wait.met:
                    halted_by = "guard"
                    guard_evidence = wait.evidence
                    results.append({"skipped": True})
                    continue
            res = await dispatcher.call_unlocked(step.tool, step.args)
            results.append(res)
            if res.get("ok"):
                completed += 1
            elif args.stop_on_failure:
                halted_by = "failure"

        out: dict = {"results": results, "completed": completed}
        if halted_by is not None:
            out["halted_by"] = halted_by
        if guard_evidence is not None:
            out["guard_evidence"] = guard_evidence
        if args.screenshot_after:
            shot = await shots.capture(fresh=True)
            # image_b64 at top level so server.py extracts it as ImageContent.
            out["image_b64"] = base64.b64encode(shot.data).decode()
            out["final_screenshot"] = shot.meta()
        return out

    registry.register(ToolSpec(
        "execute_sequence",
        "Run up to 20 PRE-DECIDED acting steps in one call (click, type, "
        "press...), each optionally gated by a guard condition (same "
        "schema as `wait`). A failed guard halts the sequence and returns "
        "evidence. Use only when you already know every step; this is a "
        "macro, not a planner.",
        SequenceArgs, execute_sequence, "act", RetryPolicy.none(),
        idempotent=False))
