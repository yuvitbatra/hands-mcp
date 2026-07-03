import pytest

from hands.errors import DriverError, InvalidArgsError
from hands.retry import RetryPolicy, execute_with_retry

pytestmark = pytest.mark.anyio


async def test_succeeds_after_transient_failures():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise DriverError("transient")
        return {"ok": True}

    policy = RetryPolicy(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)
    assert await execute_with_retry(fn, policy) == {"ok": True}
    assert len(calls) == 3


async def test_non_retryable_error_raises_immediately():
    calls = []

    async def fn():
        calls.append(1)
        raise InvalidArgsError("bad")

    with pytest.raises(InvalidArgsError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 1


async def test_side_effect_flag_blocks_retry_even_if_retryable():
    calls = []

    async def fn():
        calls.append(1)
        raise DriverError("failed mid-click", details={"side_effect": True})

    with pytest.raises(DriverError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 1


async def test_exhausts_attempts_then_raises():
    calls = []

    async def fn():
        calls.append(1)
        raise DriverError("always")

    with pytest.raises(DriverError):
        await execute_with_retry(fn, RetryPolicy(max_attempts=3,
                                                 base_delay_s=0.0))
    assert len(calls) == 3


def test_policy_presets():
    assert RetryPolicy.read().max_attempts == 3
    assert RetryPolicy.pre_side_effect().max_attempts == 3
    assert RetryPolicy.none().max_attempts == 1
