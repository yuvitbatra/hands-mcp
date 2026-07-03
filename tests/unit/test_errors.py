import pytest

from hands.errors import (
    DriverError,
    HandsError,
    InvalidArgsError,
    StaleScreenshotError,
    ToolTimeoutError,
)


def test_to_wire_includes_contract_fields():
    err = InvalidArgsError("x out of bounds", details={"x": 99999},
                           remediation="pass clamp=true")
    wire = err.to_wire()
    assert wire == {
        "code": "INVALID_ARGS",
        "message": "x out of bounds",
        "retryable": False,
        "remediation": "pass clamp=true",
        "details": {"x": 99999},
    }


def test_defaults_are_safe():
    err = HandsError("boom")
    assert err.code == "INTERNAL"
    assert err.retryable is False
    assert err.details == {}
    assert err.to_wire()["remediation"] is None


@pytest.mark.parametrize("cls,code,retryable", [
    (DriverError, "DRIVER_ERROR", True),
    (StaleScreenshotError, "STALE_SCREENSHOT", True),
    (ToolTimeoutError, "TIMEOUT", True),
])
def test_retryable_classification(cls, code, retryable):
    err = cls("x")
    assert (err.code, err.retryable) == (code, retryable)


def test_is_an_exception():
    with pytest.raises(HandsError):
        raise DriverError("capture failed")
