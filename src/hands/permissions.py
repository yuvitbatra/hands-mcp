"""Policy decisions. M1 ships AllowAllPermissions; the rule-based engine
(DESIGN §13.3) lands in M3 behind the same authorize() signature."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import PolicyDeniedError


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    tool: str
    policy_class: str


@dataclass(frozen=True, slots=True)
class Allowed:
    def raise_if_denied(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Denied:
    reason: str

    def raise_if_denied(self) -> None:
        raise PolicyDeniedError(self.reason)


class AllowAllPermissions:
    def authorize(self, action: ActionDescriptor) -> Allowed | Denied:
        return Allowed()
