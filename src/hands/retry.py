"""Declarative retries with the left-of-side-effect invariant (DESIGN §4.21)."""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio

from .errors import HandsError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    base_delay_s: float = 0.05
    max_delay_s: float = 1.0

    @classmethod
    def read(cls) -> "RetryPolicy":
        return cls(max_attempts=3)

    @classmethod
    def pre_side_effect(cls) -> "RetryPolicy":
        """Retries only errors raised BEFORE any HID event was posted.
        Services mark ambiguous failures with details['side_effect']=True;
        those are never retried (DESIGN §9.8)."""
        return cls(max_attempts=3)

    @classmethod
    def none(cls) -> "RetryPolicy":
        return cls(max_attempts=1)


async def execute_with_retry(fn: Callable[[], Awaitable[dict]],
                             policy: RetryPolicy) -> dict:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except HandsError as err:
            unsafe = bool(err.details.get("side_effect"))
            if (not err.retryable) or unsafe or attempt >= policy.max_attempts:
                raise
            delay = min(policy.max_delay_s,
                        policy.base_delay_s * 2 ** (attempt - 1))
            await anyio.sleep(random.uniform(0, delay))  # full jitter
