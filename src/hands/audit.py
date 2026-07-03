"""Append-only, hash-chained JSONL audit log (DESIGN §13.6).
line.hash = sha256(prev_hash + canonical_json(event)); tampering or
mid-file deletion breaks the chain."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      default=str)


class AuditLogger:
    def __init__(self, config) -> None:
        self._path: Path = config.security.audit_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prev = self._seed_prev()
        self._fh = self._path.open("a", encoding="utf-8")

    def _seed_prev(self) -> str:
        if not self._path.exists():
            return ""
        last = ""
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return ""
        try:
            return json.loads(last)["hash"]
        except (json.JSONDecodeError, KeyError):
            return ""

    def record(self, event: dict) -> None:
        body = _canonical(event)
        digest = hashlib.sha256(
            (self._prev + body).encode()).hexdigest()
        self._fh.write(json.dumps(
            {"event": event, "prev_hash": self._prev, "hash": digest},
            default=str) + "\n")
        self._prev = digest
        self._fh.flush()

    def flush(self) -> None:
        self._fh.flush()

    @staticmethod
    def verify_chain(path: Path) -> tuple[bool, int | None]:
        prev = ""
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    expected = hashlib.sha256(
                        (obj["prev_hash"]
                         + _canonical(obj["event"])).encode()).hexdigest()
                    if obj["hash"] != expected:
                        return False, lineno
                    if obj["prev_hash"] != prev:
                        return False, lineno
                    prev = obj["hash"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    return False, lineno
        return True, None
