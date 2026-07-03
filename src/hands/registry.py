"""ToolSpecs are data, not code (DESIGN §4.2)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .errors import InvalidArgsError
from .retry import RetryPolicy

Handler = Callable[[BaseModel, Any], Awaitable[dict[str, Any]]]
PolicyClass = Literal["read", "act", "sensitive"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str          # written for LLM consumption
    args_model: type[BaseModel]
    handler: Handler
    policy_class: PolicyClass = "act"
    retry: RetryPolicy = field(default_factory=RetryPolicy.pre_side_effect)
    idempotent: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise InvalidArgsError(
                f"unknown tool: {name}",
                details={"known": sorted(self._specs)}) from None

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def to_mcp_tools(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "description": s.description,
                 "inputSchema": s.args_model.model_json_schema()}
                for s in self._specs.values()]
