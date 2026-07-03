import pytest

from hands.driver.fake import FakeDriver


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_driver():
    return FakeDriver()
