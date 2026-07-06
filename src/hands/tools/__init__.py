from ..registry import ToolRegistry
from . import apps, ax, clipboard, observe, pointer, sequence, windows
from . import typing as typing_tools


def register_builtin_tools(registry: ToolRegistry, container) -> None:
    pointer.register(registry, container)
    typing_tools.register(registry, container)
    observe.register(registry, container)
    clipboard.register(registry, container)
    windows.register(registry, container)
    apps.register(registry, container)
    ax.register(registry, container)
    sequence.register(registry, container)
