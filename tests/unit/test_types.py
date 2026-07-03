import pytest

from hands.errors import InvalidArgsError
from hands.types import (
    KeyChord,
    ModifierFlags,
    MouseButton,
    Point,
    Region,
)


def test_region_center_and_contains():
    r = Region(10, 20, 100, 50)
    assert r.center == Point(60, 45)
    assert r.contains(Point(10, 20))
    assert r.contains(Point(109.9, 69.9))
    assert not r.contains(Point(110, 70))
    assert not r.contains(Point(9, 20))


def test_point_offset():
    assert Point(1, 2).offset(3, -1) == Point(4, 1)


def test_mouse_button_is_wire_string():
    assert MouseButton("left") is MouseButton.LEFT


def test_keychord_parse_plain_named_key():
    chord = KeyChord.parse("Return")
    assert chord.key == "Return"
    assert chord.keycode == 36
    assert chord.modifiers == ModifierFlags.NONE


def test_keychord_parse_modifiers_and_letter():
    chord = KeyChord.parse("cmd+shift+p")
    assert chord.modifiers == ModifierFlags.CMD | ModifierFlags.SHIFT
    assert chord.key == "p"
    assert chord.keycode == 35


def test_keychord_parse_alias_modifiers():
    assert KeyChord.parse("command+option+s").modifiers == (
        ModifierFlags.CMD | ModifierFlags.ALT
    )


def test_keychord_unknown_key_suggests_near_miss():
    with pytest.raises(InvalidArgsError) as ei:
        KeyChord.parse("cmd+Retrun")
    assert "Return" in str(ei.value.details.get("did_you_mean", []))


def test_keychord_empty_is_invalid():
    with pytest.raises(InvalidArgsError):
        KeyChord.parse("")


def test_textbox_center_is_clickable():
    from hands.types import TextBox

    box = TextBox("Submit", Region(100, 200, 80, 20), 0.98)
    assert box.region.center == Point(140, 210)
    assert box.confidence == 0.98
