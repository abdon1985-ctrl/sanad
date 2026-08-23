# -*- coding: utf-8 -*-
"""Append-only JSONL ledger — the proof archive.

Rules proven in EXP-000..004:
- Every entry is written once and never updated or deleted.
- The ledger is the audit source; atomicity lives in the claims table (claims.py).
- What happened is never erased; what is unresolved never becomes a new execution.
"""
import json
import os
import threading
from datetime import datetime, timezone


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, stage: str, state: str, detail: str = "", **fields) -> dict:
        row = {"ts": self._now(), "stage": stage, "state": state,
               "detail": detail, **fields}
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return row

    def rows(self) -> list:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def last(self, stage: str = None, state: str = None, **match):
        """Most recent entry matching the given fields, or None."""
        for r in reversed(self.rows()):
            if stage is not None and r.get("stage") != stage:
                continue
            if state is not None and r.get("state") != state:
                continue
            if any(r.get(k) != v for k, v in match.items()):
                continue
            return r
        return None

    def spent_today_minor(self) -> int:
        """Daily spend derived from the entries themselves — no parallel
        counter that can drift (EXP-004).

        EXP-012 (finding F3): money that left through an execution whose
        response was lost, and was later settled EXECUTED by
        reconciliation, is real spend. It is counted here via the
        `resolve` row's amount_minor — which reconciliation sets ONLY
        when no execute/EXECUTED row already counted the same money, so
        nothing is ever counted twice. Settled spend counts on the day
        it became KNOWN, not the day it happened: the ledger refuses to
        backdate what it only just learned."""
        today = self._now()[:10]
        return sum(r.get("amount_minor", 0) for r in self.rows()
                   if r.get("stage") in ("execute", "resolve")
                   and r.get("state") == "EXECUTED"
                   and r.get("ts", "").startswith(today))
