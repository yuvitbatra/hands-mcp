"""In-process counters and histograms (DESIGN §4.22)."""
from __future__ import annotations


class Metrics:
    MAX_SAMPLES = 1000

    def __init__(self) -> None:
        self._counters: dict[tuple, int] = {}
        self._histograms: dict[str, list[float]] = {}

    def inc(self, name: str, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        self._counters[key] = self._counters.get(key, 0) + 1

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._series_key(name, labels)
        samples = self._histograms.setdefault(key, [])
        samples.append(value)
        if len(samples) > self.MAX_SAMPLES:
            del samples[: len(samples) - self.MAX_SAMPLES]

    @staticmethod
    def _series_key(name: str, labels: dict) -> str:
        if not labels:
            return name
        inner = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{inner}}}"

    def snapshot(self) -> dict:
        counters = {f"{name}{dict(labels)}": v
                    for (name, labels), v in self._counters.items()}
        histograms = {}
        for key, samples in self._histograms.items():
            s = sorted(samples)
            n = len(s)
            histograms[key] = {
                "count": n,
                "sum": sum(s),
                "p50": s[int(0.50 * (n - 1))],
                "p95": s[int(0.95 * (n - 1))],
            }
        return {"counters": counters, "histograms": histograms}
