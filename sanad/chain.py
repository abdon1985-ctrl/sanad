# -*- coding: utf-8 -*-
"""Sanad chain (EXP-010) — the archive starts defending itself.

Until now "append-only" was a convention, not a property. Every claim
Sanad makes rests on the ledger being what happened; anyone with the
file could rewrite it and nothing would notice.

This module links each entry to the one before it:

    event_hash = SHA256( canonical(row) + prev_hash )

and adds a signed checkpoint that seals a height and a root.

WHAT THIS BUYS, STATED PRECISELY — because a cryptographic gesture
that implies more than it delivers is exactly what this project
exists to refuse:

  CAUGHT by the chain alone:
    - a field edited anywhere in the history
    - a row deleted from the middle
    - two rows swapped
    - a row appended without a hash (strict mode: no legacy tolerance)

  NOT CAUGHT by the chain alone:
    - TRUNCATION. Cutting entries off the tail leaves a shorter chain
      that verifies perfectly. The chain proves internal consistency,
      never completeness.
    - A FULL REWRITE by whoever holds the file. Recomputing every hash
      costs milliseconds. Local hashes cannot bind a local attacker.

Both of those are answered by the same thing, and only by it: a root
that exists somewhere the holder of the file does not control. A
signed checkpoint makes the root portable and attributable; PUBLISHING
it is what makes it evidence. Until a root is published externally,
this module raises the cost of tampering — it does not make history
immutable, and it must not be described as if it does.
"""
import hashlib
import json

from .identity import verify
from .ledger import Ledger

GENESIS = "0" * 64
CHECKPOINT_STAGE = "checkpoint"
CHECKPOINT_STATE = "SEALED"


def canonical(row: dict) -> bytes:
    """Stable bytes for a row, excluding its own hash."""
    payload = {k: v for k, v in row.items() if k != "event_hash"}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def compute_hash(row: dict) -> str:
    return hashlib.sha256(canonical(row)).hexdigest()


class ChainedLedger(Ledger):
    """A Ledger whose entries each depend on the one before."""

    def head(self) -> str:
        rows = self.rows()
        return rows[-1]["event_hash"] if rows else GENESIS

    def append(self, stage: str, state: str, detail: str = "", **fields):
        with self._lock:
            rows = self.rows()
            prev = rows[-1].get("event_hash", GENESIS) if rows else GENESIS
            row = {"ts": self._now(), "stage": stage, "state": state,
                   "detail": detail, **fields, "prev_hash": prev}
            row["event_hash"] = compute_hash(row)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


def verify_chain(ledger) -> dict:
    """Walk the chain. Returns {ok, problem, index}.

    STRICT: a row without a hash is a broken chain, not a legacy row.
    Tolerating unhashed entries would hand an attacker the trivial
    bypass of simply deleting the hash field.
    """
    rows = ledger.rows() if hasattr(ledger, "rows") else ledger
    prev = GENESIS
    for i, row in enumerate(rows):
        if "event_hash" not in row or "prev_hash" not in row:
            return {"ok": False, "index": i,
                    "problem": "UNCHAINED_ROW"}
        if row["prev_hash"] != prev:
            return {"ok": False, "index": i,
                    "problem": "BROKEN_LINK"}
        if compute_hash(row) != row["event_hash"]:
            return {"ok": False, "index": i,
                    "problem": "ALTERED_ROW"}
        prev = row["event_hash"]
    return {"ok": True, "index": None, "problem": None,
            "height": len(rows), "root": prev}


def seal(ledger: ChainedLedger, signer) -> dict:
    """Append a signed checkpoint over (height, root).

    The checkpoint is itself a chained row, so it cannot be lifted out
    of the history it seals.
    """
    rows = ledger.rows()
    height = len(rows)
    root = rows[-1]["event_hash"] if rows else GENESIS
    material = f"{height}:{root}".encode("utf-8")
    return ledger.append(
        CHECKPOINT_STAGE, CHECKPOINT_STATE,
        f"sealed height {height} by '{signer.name}'",
        height=height, root=root,
        sealed_by=signer.name,
        signature=signer.sign(material))


def verify_checkpoints(ledger, trusted_keys: dict) -> dict:
    """Every checkpoint must be signed by a trusted key AND must name
    the root that the chain actually had at that height."""
    rows = ledger.rows()
    for i, row in enumerate(rows):
        if row.get("stage") != CHECKPOINT_STAGE:
            continue
        who = row.get("sealed_by")
        if who not in trusted_keys:
            return {"ok": False, "index": i, "problem": "UNTRUSTED_SEALER"}
        material = f"{row['height']}:{row['root']}".encode("utf-8")
        if not verify(trusted_keys[who], material, row.get("signature", "")):
            return {"ok": False, "index": i, "problem": "BAD_SEAL_SIGNATURE"}
        expected = (rows[row["height"] - 1]["event_hash"]
                    if row["height"] else GENESIS)
        if expected != row["root"]:
            return {"ok": False, "index": i, "problem": "SEAL_ROOT_MISMATCH"}
    return {"ok": True, "index": None, "problem": None}


def verify_against_published(ledger, height: int, root: str) -> dict:
    """The only check that survives an attacker holding the file.

    `height` and `root` must come from somewhere the file's holder does
    not control — a published checkpoint, a counterparty's copy, a
    timestamping service. Without that, this function has nothing to
    compare against and neither does anyone else.
    """
    rows = ledger.rows()
    if len(rows) < height:
        return {"ok": False, "problem": "TRUNCATED",
                "detail": f"published height {height}, file has {len(rows)}"}
    actual = rows[height - 1]["event_hash"] if height else GENESIS
    if actual != root:
        return {"ok": False, "problem": "DIVERGED_HISTORY",
                "detail": f"row {height - 1} does not match published root"}
    return {"ok": True, "problem": None, "detail": None}
