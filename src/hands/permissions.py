"""Policy layer (DESIGN §7.10, §13.3). OS/TCC permissions are a different
layer (PermissionMissingError); this module only decides what the AGENT
may do (PolicyDeniedError)."""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal

import anyio

from .errors import PolicyDeniedError


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    tool: str
    policy_class: str
    target_app: str | None = None      # frontmost bundle id at call time
    text: str | None = None            # typed/pasted text, if any


@dataclass(frozen=True)
class Allowed:
    def raise_if_denied(self) -> None:
        pass


@dataclass(frozen=True)
class Denied:
    reason: str

    def raise_if_denied(self) -> None:
        raise PolicyDeniedError(self.reason)


@dataclass(frozen=True)
class NeedsConfirmation:
    prompt: str

    def raise_if_denied(self) -> None:
        pass


@dataclass(frozen=True)
class Rule:
    match_tools: tuple[str, ...] = ("*",)
    match_apps: tuple[str, ...] = ("*",)
    match_text: str | None = None
    effect: Literal["allow", "deny", "confirm"] = "allow"

    def matches(self, action: ActionDescriptor) -> bool:
        if not any(fnmatch(action.tool, g) for g in self.match_tools):
            return False
        app = action.target_app or ""
        if not any(fnmatch(app, g) for g in self.match_apps):
            return False
        if self.match_text is not None:
            if action.text is None:
                return False
            if not re.search(self.match_text, action.text):
                return False
        return True


@dataclass(frozen=True)
class Profile:
    """First matching rule wins; then per-class defaults (DESIGN §13.3)."""
    name: str
    rules: tuple[Rule, ...] = ()
    confirm_acts: bool = False
    allow_sensitive: bool = False


def load_profile(config) -> Profile:
    name = config.security.profile
    if name == "strict":
        return Profile("strict", confirm_acts=True)
    if name == "trusted":
        return Profile("trusted", allow_sensitive=True)
    return Profile("default")


ConfirmationHook = Callable[[str, ActionDescriptor], Awaitable[bool]]


async def auto_deny_hook(prompt: str, action: ActionDescriptor) -> bool:
    return False


async def osascript_hook(prompt: str, action: ActionDescriptor) -> bool:
    """macOS confirmation dialog. Runs off the event loop."""
    def _ask() -> bool:
        script = (
            'display dialog "{}" with title "Hands" '
            'buttons {{"Deny", "Allow"}} default button "Deny"'
        ).format(prompt.replace('"', "'"))
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=60)
        return proc.returncode == 0 and "Allow" in proc.stdout
    return await anyio.to_thread.run_sync(_ask)


def make_confirm_hook(config) -> ConfirmationHook:
    if config.security.confirmation == "dialog" \
            and sys.platform == "darwin":
        return osascript_hook
    return auto_deny_hook


class PermissionEngine:
    def __init__(self, profile: Profile, confirm_hook: ConfirmationHook,
                 config) -> None:
        self._profile = profile
        self._hook = confirm_hook
        self._sec = config.security

    def authorize(self, action: ActionDescriptor):
        # 1. deny-listed apps block anything that acts on them
        if action.policy_class != "read" and action.target_app:
            for glob in self._sec.deny_apps:
                if fnmatch(action.target_app, glob):
                    return Denied(
                        f"{action.target_app} is deny-listed "
                        f"(matched {glob!r})")
        # 2. explicit profile rules, first match wins
        for rule in self._profile.rules:
            if rule.matches(action):
                if rule.effect == "deny":
                    return Denied(f"denied by profile rule for "
                                  f"{action.tool}")
                if rule.effect == "confirm":
                    return NeedsConfirmation(self._prompt(action))
                return Allowed()
        # 3. secret patterns in typed text
        if action.text is not None:
            for pattern in self._sec.secret_patterns:
                if re.search(pattern, action.text):
                    return NeedsConfirmation(
                        f"{action.tool} would type text matching a "
                        f"secret pattern. Allow?")
        # 4. class defaults
        if action.policy_class == "read":
            return Allowed()
        if action.policy_class == "act":
            if self._profile.confirm_acts:
                return NeedsConfirmation(self._prompt(action))
            return Allowed()
        if self._profile.allow_sensitive:
            return Allowed()
        return NeedsConfirmation(self._prompt(action))

    async def confirm(self, prompt: str,
                      action: ActionDescriptor) -> bool:
        return await self._hook(prompt, action)

    @staticmethod
    def _prompt(action: ActionDescriptor) -> str:
        target = f" on {action.target_app}" if action.target_app else ""
        return f"Allow the agent to run {action.tool}{target}?"


class AllowAllPermissions:
    """M1 stub, kept for tests and headless setups."""

    def authorize(self, action: ActionDescriptor):
        return Allowed()

    async def confirm(self, prompt: str,
                      action: ActionDescriptor) -> bool:
        return True
