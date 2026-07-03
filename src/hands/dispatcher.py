"""The 7-phase pipeline (DESIGN §2.5). Every tool call flows through here."""
from __future__ import annotations

import time
import uuid
from typing import Any

import anyio
import structlog
from pydantic import BaseModel, ValidationError

from .audit import AuditLogger
from .config import HandsConfig
from .errors import (
    HandsError,
    InvalidArgsError,
    KillSwitchError,
    StaleScreenshotError,
)
from .metrics import Metrics
from .permissions import ActionDescriptor
from .registry import ToolRegistry, ToolSpec
from .retry import execute_with_retry
from .state import ActionRecord, StateManager

log = structlog.get_logger(__name__)


class Dispatcher:
    def __init__(self, registry: ToolRegistry, permissions: Any,
                 state: StateManager, audit: AuditLogger, metrics: Metrics,
                 config: HandsConfig) -> None:
        self._registry = registry
        self._permissions = permissions
        self._state = state
        self._audit = audit
        self._metrics = metrics
        self._config = config
        self._action_lock = anyio.Lock()  # HID is a global shared resource

    async def dispatch(self, tool_name: str, raw_args: dict[str, Any],
                       ctx: Any = None) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            spec = self._registry.get(tool_name)                 # phase 1
            args = self._validate(spec, raw_args)
            self._preflight(args)                                # phase 2
            policy_class = spec.policy_class
            if spec.escalate is not None and spec.escalate(args):
                policy_class = "sensitive"
            self._permissions.authorize(ActionDescriptor(        # phase 3
                tool=spec.name,
                policy_class=policy_class)).raise_if_denied()

            async def call() -> dict[str, Any]:
                return await spec.handler(args, ctx)

            if spec.policy_class == "read":                      # phases 4-5
                result = await execute_with_retry(call, spec.retry)
            else:
                async with self._action_lock:
                    result = await execute_with_retry(call, spec.retry)

            duration = time.monotonic() - started                # phase 6
            self._state.record_action(ActionRecord.ok(
                request_id, tool_name, args.model_dump(), duration))
            if spec.policy_class != "read":
                self._state.mark_screen_dirty()

            self._finish(request_id, tool_name, "ok")            # phase 7
            return {"ok": True, "request_id": request_id, **result}

        except HandsError as err:
            self._state.record_action(ActionRecord.failed(
                request_id, tool_name, raw_args or {}, err))
            self._finish(request_id, tool_name, err.code)
            return {"ok": False, "request_id": request_id,
                    "error": err.to_wire()}
        except Exception:
            log.exception("internal_error", tool=tool_name,
                          request_id=request_id)
            self._finish(request_id, tool_name, "INTERNAL")
            return {"ok": False, "request_id": request_id,
                    "error": {"code": "INTERNAL", "retryable": False,
                              "message": f"internal error {request_id}",
                              "remediation": None, "details": {}}}

    def _validate(self, spec: ToolSpec, raw_args: dict[str, Any]) -> BaseModel:
        try:
            return spec.args_model.model_validate(raw_args or {})
        except ValidationError as e:
            raise InvalidArgsError(
                f"invalid arguments for {spec.name}",
                details={"errors": e.errors(include_url=False)}) from None

    def _preflight(self, args: BaseModel) -> None:
        if self._config.security.kill_switch_engaged():
            raise KillSwitchError(
                "kill switch engaged",
                remediation=f"remove {self._config.security.kill_switch_path}")
        if not hasattr(args, "require_fresh_screenshot"):
            return
        required = args.require_fresh_screenshot
        if required is None:
            required = self._config.observe.require_fresh_default
        if not required:
            return
        meta = self._state.latest_screenshot_meta
        max_age = self._config.observe.max_screenshot_age_s
        if (meta is None or self._state.screen_dirty
                or time.monotonic() - meta["ts"] > max_age):
            raise StaleScreenshotError(
                "coordinate action requires a fresh screenshot",
                remediation="call the screenshot tool, then retry")

    def _finish(self, request_id: str, tool: str, outcome: str) -> None:
        self._audit.record({"request_id": request_id, "tool": tool,
                            "outcome": outcome})
        self._metrics.inc("tool_calls_total", tool=tool, outcome=outcome)
