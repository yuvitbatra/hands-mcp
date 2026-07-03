import pytest

from hands.config import HandsConfig
from hands.errors import PolicyDeniedError
from hands.services.clipboard import ClipboardService
from hands.services.keyboard import KeyboardService
from hands.types import ClipboardContent

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(fake_driver):
    cfg = HandsConfig()
    cfg.clipboard.restore_delay_ms = 0
    return ClipboardService(fake_driver,
                            KeyboardService(fake_driver, cfg), cfg)


async def test_set_get_round_trip(service):
    await service.set(ClipboardContent("text", text="hi"))
    got = await service.get()
    assert got.kind == "text" and got.text == "hi"


async def test_get_wrong_format_is_empty(service):
    await service.set(ClipboardContent("text", text="hi"))
    assert (await service.get("image")).kind == "empty"


async def test_get_refuses_during_secure_input(fake_driver, service):
    fake_driver.set_secure_input(True)
    with pytest.raises(PolicyDeniedError):
        await service.get()


async def test_paste_sets_presses_cmd_v_and_restores(fake_driver, service):
    await service.set(ClipboardContent("text", text="original"))
    fake_driver.pop_events()
    await service.paste("pasted")
    events = fake_driver.pop_events()
    kinds = [e[0] for e in events]
    # write(pasted), key events for cmd+v, write(original) — in that order.
    assert kinds[0] == "clipboard_write"
    assert "key" in kinds
    assert kinds[-1] == "clipboard_write"
    assert (await service.get()).text == "original"


async def test_paste_no_restore(fake_driver, service):
    await service.set(ClipboardContent("text", text="original"))
    await service.paste("pasted", restore=False)
    assert (await service.get()).text == "pasted"
