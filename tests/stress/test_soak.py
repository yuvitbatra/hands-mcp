"""Soak + concurrency stress (DESIGN §12). Run:
    uv run pytest tests/stress -m stress -q
"""
from __future__ import annotations

import tracemalloc

import anyio
import pytest

from hands.config import HandsConfig
from hands.container import Container

pytestmark = [pytest.mark.stress, pytest.mark.anyio]


@pytest.fixture
def container(tmp_path):
    cfg = HandsConfig()
    cfg.driver = "fake"
    cfg.security.profile = "trusted"
    cfg.security.kill_switch_path = tmp_path / "KILL"
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    cfg.mouse.click_delay_ms = 0
    return Container.build(cfg)


async def test_10k_actions_do_not_leak(container):
    d = container.dispatcher

    async def burst(n: int) -> None:
        for i in range(n):
            r = await d.dispatch("mouse_move",
                                 {"x": i % 1000, "y": i % 800})
            assert r["ok"]
            if i % 20 == 0:
                assert (await d.dispatch("screenshot",
                                         {"fresh": True}))["ok"]

    tracemalloc.start()
    await burst(1000)                          # warm up all caches
    baseline, _ = tracemalloc.get_traced_memory()
    await burst(9000)
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    growth_mb = (current - baseline) / 1e6
    assert growth_mb < 10, f"leaked {growth_mb:.1f} MB over 9k actions"
    # bounded state (DESIGN §8.1)
    assert len(container.state.history(10_000)) \
        <= container.config.state.history_len


async def test_reads_during_drag_do_not_corrupt_events(container):
    d = container.dispatcher
    driver = container.driver
    driver.pop_events()
    read_results: list[bool] = []

    async def reader() -> None:
        for _ in range(25):
            r = await d.dispatch("screenshot", {"fresh": True})
            read_results.append(r["ok"])

    async with anyio.create_task_group() as tg:
        tg.start_soon(reader)
        tg.start_soon(reader)
        res = await d.dispatch("mouse_drag", {
            "path": [{"x": 0, "y": 0}, {"x": 500, "y": 500}],
            "duration_ms": 200})
        assert res["ok"]

    assert all(read_results) and len(read_results) == 50
    mouse_events = [e[1] for e in driver.pop_events() if e[0] == "mouse"]
    # containment invariant: move/down first, up last, moves between
    assert mouse_events[0].kind in ("move", "down")
    assert mouse_events[-1].kind == "up"
    kinds = [e.kind for e in mouse_events]
    assert kinds.count("down") == 1 and kinds.count("up") == 1
