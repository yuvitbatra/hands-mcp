import json

from hands.audit import AuditLogger


def _logger(tmp_path):
    from hands.config import HandsConfig
    cfg = HandsConfig()
    cfg.security.audit_path = tmp_path / "audit.jsonl"
    return cfg, AuditLogger(cfg)


def test_chain_verifies(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"tool": "mouse_move", "n": i})
    log.flush()
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is True and bad is None


def test_chain_survives_restart(tmp_path):
    cfg, log = _logger(tmp_path)
    log.record({"n": 1})
    log.flush()
    log2 = AuditLogger(cfg)          # new process, same file
    log2.record({"n": 2})
    log2.flush()
    ok, _ = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok


def test_tampering_detected(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"n": i})
    log.flush()
    lines = cfg.security.audit_path.read_text().splitlines()
    doctored = json.loads(lines[1])
    doctored["event"]["n"] = 999
    lines[1] = json.dumps(doctored)
    cfg.security.audit_path.write_text("\n".join(lines) + "\n")
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is False and bad == 2


def test_truncation_detected(tmp_path):
    cfg, log = _logger(tmp_path)
    for i in range(3):
        log.record({"n": i})
    log.flush()
    lines = cfg.security.audit_path.read_text().splitlines()
    # Keep 2 of the 3 lines (drop only the last) so that, after the
    # logger re-seeds and appends one more record below, the file has
    # 3 lines total -- i.e. an actual middle line exists to remove in
    # the second half of this test. (Truncating to 1 line would leave
    # only 2 lines after reseeding, with no middle line to remove.)
    cfg.security.audit_path.write_text("\n".join(lines[:2]) + "\n")
    log2 = AuditLogger(cfg)
    log2.record({"n": "after-truncation"})
    log2.flush()
    ok, _ = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok  # truncation from the END re-seeds cleanly...
    # ...but removing a MIDDLE line breaks the chain:
    lines = cfg.security.audit_path.read_text().splitlines()
    cfg.security.audit_path.write_text(
        "\n".join([lines[0], lines[-1]]) + "\n")
    ok, bad = AuditLogger.verify_chain(cfg.security.audit_path)
    assert ok is False
