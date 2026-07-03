from ..registry import ToolRegistry
from . import clipboard, observe, pointer
from . import typing as typing_tools


def register_builtin_tools(registry: ToolRegistry, container) -> None:
    pointer.register(registry, container)
    typing_tools.register(registry, container)
    observe.register(registry, container)
    clipboard.register(registry, container)
