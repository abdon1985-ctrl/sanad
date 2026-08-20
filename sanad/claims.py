# -*- coding: utf-8 -*-
"""Atomic claim — an approval is consumed exactly once.

Rules proven in EXP-003 (20 rounds x 10 concurrent workers, zero failures):
- The SQLite UNIQUE-constraint INSERT is the single source of atomicity and the
  race arbiter. The loser of a race receives IntegrityError, never a duplicate.
- Order of writes: (1) INSERT claim in SQLite  (2) claim line in the ledger
  (3) external call  (4) status update + result line.
  Whoever owns atomicity is written first; the ledger is proof, not recovery.
- A claim with no terminal status stays PENDING and is settled by reconcile —
  never by reusing the approval.
"""
import sqlite3


class ClaimStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS executions(
                approval_id  TEXT PRIMARY KEY,
                execution_id TEXT UNIQUE NOT NULL,
                status       TEXT NOT NULL DEFAULT 'PENDING')""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_status ON executions(status)")

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=5)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def try_claim(self, approval_id: str, execution_id: str) -> bool:
        """Atomic. True = won the race. False = approval already consumed."""
        c = self._conn()
        try:
            c.execute("INSERT INTO executions VALUES (?,?,'PENDING')",
                      (approval_id, execution_id))
            c.commit()
            return True
        except sqlite3.IntegrityError:
            c.rollback()
            return False
        finally:
            c.close()

    def set_status(self, execution_id: str, status: str):
        c = self._conn()
        c.execute("UPDATE executions SET status=? WHERE execution_id=?",
                  (status, execution_id))
        c.commit()
        c.close()

    def unresolved(self) -> list:
        """PENDING and UNRESOLVED both re-enter recovery — UNRESOLVED is
        'settlement failed this time', not a terminal state (EXP-003)."""
        c = self._conn()
        rows = c.execute(
            "SELECT approval_id, execution_id FROM executions "
            "WHERE status IN ('PENDING','UNRESOLVED')").fetchall()
        c.close()
        return rows

    def status_of(self, execution_id: str):
        c = self._conn()
        row = c.execute("SELECT status FROM executions WHERE execution_id=?",
                        (execution_id,)).fetchone()
        c.close()
        return row[0] if row else None
