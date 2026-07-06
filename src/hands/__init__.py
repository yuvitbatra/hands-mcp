"""Hands — macOS computer-use MCP server. See docs/DESIGN.md."""

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "Container",
    "HandsConfig",
    "load_config",
    "build_server",
    "run_server",
    "HandsError",
    "InvalidArgsError",
    "PolicyDeniedError",
    "TargetNotFoundError",
]

from .config import HandsConfig, load_config
from .container import Container
from .errors import (
    HandsError,
    InvalidArgsError,
    PolicyDeniedError,
    TargetNotFoundError,
)
from .server import build_server, run_server
