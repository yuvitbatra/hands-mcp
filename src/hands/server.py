"""MCP server assembly over the low-level SDK Server (plan: Global
Constraints, FastMCP deviation note)."""
from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import HandsConfig
from .container import Container


def build_server(container: Container) -> Server:
    server = Server("hands")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=s.name, description=s.description,
                           inputSchema=s.args_model.model_json_schema())
                for s in container.registry.list_specs()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None
                        ) -> list[types.TextContent | types.ImageContent]:
        result = await container.dispatcher.dispatch(name, arguments or {})
        blocks: list[types.TextContent | types.ImageContent] = []
        image_b64 = result.pop("image_b64", None)
        if image_b64:
            mime = f"image/{result.get('fmt', 'png')}"
            blocks.append(types.ImageContent(type="image", data=image_b64,
                                             mimeType=mime))
        blocks.append(types.TextContent(type="text",
                                        text=json.dumps(result)))
        return blocks

    return server


async def run_server(config: HandsConfig) -> None:
    container = Container.build(config)
    server = build_server(container)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())
    finally:
        # A crash mid-hotkey must not leave cmd held down (DESIGN §2.6).
        container.keyboard.release_all()
