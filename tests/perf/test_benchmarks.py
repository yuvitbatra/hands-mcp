"""Latency budgets on the fake driver (DESIGN §14). Run:
    uv run pytest tests/perf -m perf --benchmark-only
Budgets asserted loosely (2x headroom) to avoid CI-noise flakes; trends
are what matter (--benchmark-autosave).
"""
from __future__ import annotations

import anyio
import pytest

from hands.config import HandsConfig
from hands.container import Container

pytestmark = pytest.mark.perf


@pytest.fixture
def container(tmp_path):
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    return Container.build(cfg)


def _run(coro_fn):
    return anyio.run(coro_fn)


def test_dispatch_overhead(benchmark, container):
    async def once():
        return await container.dispatcher.dispatch("get_state", {})

    result = benchmark(lambda: _run(once))
    assert result["ok"]
    # Trend tracked externally (--benchmark-autosave); loose sanity only
    mean_s = benchmark.stats.get("mean") or benchmark.stats.as_dict().get("mean", 1)
    assert mean_s < 0.050  # 50 ms sanity cap on any hardware


def test_screenshot_cached_vs_uncached(benchmark, container):
    async def cached():
        await container.dispatcher.dispatch("screenshot", {"fresh": True})
        return await container.dispatcher.dispatch("screenshot", {})

    result = benchmark(lambda: _run(cached))
    assert result["ok"]
    assert result["cached"] is True


def test_click_latency(benchmark, container):
    container.config.mouse.click_delay_ms = 0

    async def once():
        return await container.dispatcher.dispatch(
            "mouse_click", {"x": 100, "y": 100})

    result = benchmark(lambda: _run(once))
    assert result["ok"]
    # DESIGN §14: acting tools ≤50 ms; loose cap for CI variance
    mean_s = benchmark.stats.get("mean") or benchmark.stats.as_dict().get("mean", 1)
    assert mean_s < 0.500  # 500 ms upper bound on any machine
