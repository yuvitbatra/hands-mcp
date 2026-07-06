import pytest

from hands.config import HandsConfig
from hands.permissions import (
    ActionDescriptor,
    Allowed,
    Denied,
    NeedsConfirmation,
    PermissionEngine,
    Profile,
    Rule,
    load_profile,
)

pytestmark = pytest.mark.anyio


async def _yes(prompt, action):
    return True


def _engine(profile=None, cfg=None, hook=_yes):
    cfg = cfg or HandsConfig()
    return PermissionEngine(profile or load_profile(cfg), hook, cfg)


def test_read_allowed_by_default():
    d = _engine().authorize(ActionDescriptor("screenshot", "read"))
    assert isinstance(d, Allowed)


def test_act_allowed_default_confirmed_under_strict():
    cfg = HandsConfig()
    assert isinstance(
        _engine().authorize(ActionDescriptor("mouse_click", "act")),
        Allowed)
    cfg.security.profile = "strict"
    assert isinstance(
        _engine(load_profile(cfg), cfg).authorize(
            ActionDescriptor("mouse_click", "act")),
        NeedsConfirmation)


def test_sensitive_confirms_by_default_allowed_when_trusted():
    assert isinstance(
        _engine().authorize(ActionDescriptor("clipboard_get",
                                             "sensitive")),
        NeedsConfirmation)
    cfg = HandsConfig()
    cfg.security.profile = "trusted"
    assert isinstance(
        _engine(load_profile(cfg), cfg).authorize(
            ActionDescriptor("clipboard_get", "sensitive")),
        Allowed)


def test_deny_listed_app_blocks_acting_tools():
    d = _engine().authorize(ActionDescriptor(
        "mouse_click", "act", target_app="com.apple.Passwords"))
    assert isinstance(d, Denied)
    # reads are not blocked by the app deny list
    assert isinstance(_engine().authorize(ActionDescriptor(
        "screenshot", "read", target_app="com.apple.Passwords")),
        Allowed)


def test_first_matching_rule_wins():
    profile = Profile("custom", rules=(
        Rule(match_tools=("keyboard_*",), effect="deny"),
        Rule(match_tools=("*",), effect="allow"),
    ))
    engine = _engine(profile)
    assert isinstance(engine.authorize(
        ActionDescriptor("keyboard_type", "act")), Denied)
    assert isinstance(engine.authorize(
        ActionDescriptor("mouse_click", "act")), Allowed)


def test_secret_pattern_forces_confirmation():
    cfg = HandsConfig()
    cfg.security.secret_patterns = [r"(?i)password"]
    engine = _engine(load_profile(cfg), cfg)
    d = engine.authorize(ActionDescriptor(
        "keyboard_type", "act", text="my Password123"))
    assert isinstance(d, NeedsConfirmation)


async def test_confirm_delegates_to_hook():
    calls = []

    async def hook(prompt, action):
        calls.append(prompt)
        return False

    engine = _engine(hook=hook)
    ok = await engine.confirm("Allow?", ActionDescriptor("x", "sensitive"))
    assert ok is False and calls == ["Allow?"]
