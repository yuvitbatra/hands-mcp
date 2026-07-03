"""Composition root (DESIGN §3.1). Builds every service exactly once."""
from __future__ import annotations

import sys

from .audit import AuditLogger
from .config import HandsConfig
from .dispatcher import Dispatcher
from .driver.base import Driver
from .metrics import Metrics
from .permissions import AllowAllPermissions
from .registry import ToolRegistry
from .services.clipboard import ClipboardService
from .services.coords import CoordinateMapper
from .services.keyboard import KeyboardService
from .services.mouse import MouseService
from .services.ocr import OCRService
from .services.screenshot import ScreenshotService
from .services.verification import VerificationEngine
from .services.waiter import Waiter
from .services.windows import WindowService
from .state import StateManager
from .tools import register_builtin_tools


def _make_driver(config: HandsConfig) -> Driver:
    choice = config.driver
    if choice == "auto":
        choice = "macos" if sys.platform == "darwin" else "fake"
    if choice == "fake":
        from .driver.fake import FakeDriver
        return FakeDriver()
    from .driver.macos import MacOSDriver
    return MacOSDriver()


class Container:
    @classmethod
    def build(cls, config: HandsConfig) -> "Container":
        self = cls()
        self.config = config
        self.driver = _make_driver(config)
        self.state = StateManager(config)
        self.coords = CoordinateMapper(self.driver.displays())
        self.screenshots = ScreenshotService(self.driver, self.state, config)
        self.ocr = OCRService(self.driver, self.coords, config)
        self.waiter = Waiter(self.screenshots, self.ocr, config)
        self.verification = VerificationEngine(
            self.screenshots, self.ocr, self.driver, config)
        self.mouse = MouseService(self.driver, self.coords, self.state,
                                  config)
        self.keyboard = KeyboardService(self.driver, config)
        self.clipboard = ClipboardService(self.driver, self.keyboard,
                                          config)
        self.windows = WindowService(self.driver)
        self.audit = AuditLogger(config)
        self.metrics = Metrics()
        self.permissions = AllowAllPermissions()
        self.registry = ToolRegistry()
        register_builtin_tools(self.registry, self)
        self.dispatcher = Dispatcher(self.registry, self.permissions,
                                     self.state, self.audit, self.metrics,
                                     config)
        return self
