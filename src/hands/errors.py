"""Single exception hierarchy; codes are the wire contract (DESIGN §4.20)."""
from __future__ import annotations

from typing import Any


class HandsError(Exception):
    code: str = "INTERNAL"
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None,
                 remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.remediation = remediation

    def to_wire(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "remediation": self.remediation,
            "details": self.details,
        }


class InvalidArgsError(HandsError):
    code = "INVALID_ARGS"


class PermissionMissingError(HandsError):
    """OS-level TCC grant missing; remediation carries a settings deep link."""
    code = "PERMISSION_MISSING"


class PolicyDeniedError(HandsError):
    code = "POLICY_DENIED"


class KillSwitchError(HandsError):
    code = "KILL_SWITCH"


class TargetNotFoundError(HandsError):
    code = "TARGET_NOT_FOUND"
    retryable = True


class StaleScreenshotError(HandsError):
    code = "STALE_SCREENSHOT"
    retryable = True


class DriverError(HandsError):
    code = "DRIVER_ERROR"
    retryable = True


class ToolTimeoutError(HandsError):
    code = "TIMEOUT"
    retryable = True
