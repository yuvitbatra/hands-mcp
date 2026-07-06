from hands.cli import main


def test_permissions_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("HANDS_DRIVER", "fake")
    assert main(["permissions"]) == 0
    out = capsys.readouterr().out
    assert "screen_recording" in out and "accessibility" in out


def test_doctor_metrics_flag(monkeypatch, capsys):
    monkeypatch.setenv("HANDS_DRIVER", "fake")
    assert main(["doctor", "--metrics"]) == 0
    out = capsys.readouterr().out
    assert "counters" in out and "histograms" in out
