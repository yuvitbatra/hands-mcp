import pytest

from hands.config import HandsConfig
from hands.errors import DriverError
from hands.services.keyboard import KeyboardService
from hands.types import KeyChord, ModifierFlags

pytestmark = pytest.mark.anyio

CMD_KEYCODE = 55
SHIFT_KEYCODE = 56


@pytest.fixture
def svc(fake_driver):
    cfg = HandsConfig()
    cfg.keyboard.chunk_delay_ms = 0
    cfg.keyboard.chunk_size = 4
    return KeyboardService(fake_driver, cfg), fake_driver


async def test_type_text_chunks_and_counts(svc):
    service, driver = svc
    n = await service.type_text("hello world")
    assert n == 11
    assert driver.typed_text() == "hello world"
    chunks = [e[1] for e in driver.pop_events() if e[0] == "type"]
    assert chunks == ["hell", "o wo", "rld"]


async def test_type_text_midstream_failure_reports_progress(svc):
    service, driver = svc
    calls = {"n": 0}
    original = driver.type_unicode

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise DriverError("dropped")
        original(text)

    driver.type_unicode = flaky
    with pytest.raises(DriverError) as ei:
        await service.type_text("hello world")
    assert ei.value.details["chars_typed"] == 4
    assert ei.value.details["side_effect"] is True


async def test_press_holds_then_releases_modifiers(svc):
    service, driver = svc
    await service.press(KeyChord.parse("cmd+shift+s"))
    keys = [e for e in driver.pop_events() if e[0] == "key"]
    downs = [(k, d) for _, k, d, _ in keys]
    # modifiers down, key down+up, modifiers up (order within mods stable)
    assert (CMD_KEYCODE, True) in downs and (SHIFT_KEYCODE, True) in downs
    assert (CMD_KEYCODE, False) in downs and (SHIFT_KEYCODE, False) in downs
    assert downs.index((CMD_KEYCODE, False)) > downs.index((1, True))  # 's'=1


async def test_modifiers_released_even_when_key_post_fails(svc):
    service, driver = svc
    calls = {"n": 0}
    original = driver.post_key

    def flaky(keycode, down, flags):
        calls["n"] += 1
        if keycode not in (CMD_KEYCODE, SHIFT_KEYCODE) and down:
            raise DriverError("tap failed")
        original(keycode, down, flags)

    driver.post_key = flaky
    with pytest.raises(DriverError):
        await service.press(KeyChord.parse("cmd+s"))
    keys = [(k, d) for e, k, d, _ in driver.pop_events() if e == "key"]
    assert (CMD_KEYCODE, False) in keys      # released despite failure


async def test_repeat(svc):
    service, driver = svc
    await service.press(KeyChord.parse("Down"), repeat=3)
    keys = [e for e in driver.pop_events() if e[0] == "key"]
    assert len([k for k in keys if k[2] is True]) == 3


async def test_type_text_refuses_during_secure_input(fake_driver):
    from hands.errors import PolicyDeniedError

    cfg = HandsConfig()
    service = KeyboardService(fake_driver, cfg)
    fake_driver.set_secure_input(True)
    with pytest.raises(PolicyDeniedError):
        await service.type_text("hunter2")
    assert fake_driver.typed_text() == ""
