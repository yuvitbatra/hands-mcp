import pytest
from pydantic import BaseModel

from hands.registry import ToolRegistry, ToolSpec
from hands.retry import RetryPolicy


class NoArgs(BaseModel, extra="forbid"):
    pass


async def _noop(args, ctx):
    return {}


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="d", args_model=NoArgs,
                    handler=_noop, policy_class="read",
                    retry=RetryPolicy.none(), idempotent=True)


def test_register_get_and_list():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    assert reg.get("a").name == "a"
    assert [s.name for s in reg.list_specs()] == ["a"]


def test_duplicate_name_rejected():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    with pytest.raises(ValueError):
        reg.register(_spec("a"))


def test_unknown_tool_is_invalid_args():
    from hands.errors import InvalidArgsError
    with pytest.raises(InvalidArgsError):
        ToolRegistry().get("nope")


def test_to_mcp_tools_serializes_schema():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    (tool,) = reg.to_mcp_tools()
    assert tool["name"] == "a"
    assert tool["inputSchema"]["additionalProperties"] is False
