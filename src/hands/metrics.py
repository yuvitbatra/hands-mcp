"""In-process counters (DESIGN §4.22; histograms/OTLP land in M3)."""
from __future__ import annotations


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[tuple, int] = {}

    def inc(self, name: str, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        self._counters[key] = self._counters.get(key, 0) + 1

    def snapshot(self) -> dict[str, int]:
        return {f"{name}{dict(labels)}": v
                for (name, labels), v in self._counters.items()}
