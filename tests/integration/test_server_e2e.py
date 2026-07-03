import json

import pytest
from mcp.shared.memory import (
    create_connected_server_and_client_session as connect,
)

from hands.config import HandsConfig
from hands.container import Container
from hands.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def server(tmp_path):
    cfg = HandsConfig(driver="fake")
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    cfg.mouse.click_delay_ms = 0
    return build_server(Container.build(cfg))


async def test_lists_all_builtin_tools(server):
    async with connect(server) as client:
        tools = (await client.list_tools()).tools
        names = {t.name for t in tools}
        assert {"screenshot", "get_state", "wait", "mouse_move",
                "mouse_click", "mouse_drag", "mouse_scroll",
                "keyboard_type", "key_press"} <= names


async def test_screenshot_returns_image_block_plus_json(server):
    async with connect(server) as client:
        res = await client.call_tool("screenshot", {})
        kinds = [c.type for c in res.content]
        assert "image" in kinds and "text" in kinds
        meta = json.loads(
            next(c.text for c in res.content if c.type == "text"))
        assert meta["ok"] is True
        assert meta["px_per_pt"] > 0
        assert "image_b64" not in meta   # pixels live in the image block


async def test_click_roundtrip(server):
    async with connect(server) as client:
        res = await client.call_tool("mouse_click", {"x": 10, "y": 20})
        payload = json.loads(res.content[-1].text)
        assert payload["ok"] is True
        assert payload["cursor"] == {"x": 10, "y": 20}


async def test_error_envelope_over_the_wire(server):
    async with connect(server) as client:
        res = await client.call_tool("mouse_click", {"x": 1e9, "y": 1e9})
        payload = json.loads(res.content[-1].text)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGS"
