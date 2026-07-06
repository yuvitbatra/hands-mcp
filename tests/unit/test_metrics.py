from hands.metrics import Metrics


def test_counter_inc_and_snapshot():
    m = Metrics()
    m.inc("tool_calls_total", tool="x", outcome="ok")
    m.inc("tool_calls_total", tool="x", outcome="ok")
    snap = m.snapshot()
    assert snap["counters"]["tool_calls_total{'outcome': 'ok', 'tool': 'x'}"] == 2


def test_histogram_observe_and_snapshot():
    m = Metrics()
    for v in [0.010, 0.020, 0.030, 0.040, 0.100]:
        m.observe("tool_seconds", v, tool="screenshot")
    snap = m.snapshot()
    series = snap["histograms"]["tool_seconds{tool=screenshot}"]
    assert series["count"] == 5
    assert 0.02 <= series["p50"] <= 0.04
    assert series["p95"] <= 0.1


def test_histogram_bounded():
    m = Metrics()
    for i in range(2000):
        m.observe("x", float(i))
    assert m.snapshot()["histograms"]["x"]["count"] == 1000
