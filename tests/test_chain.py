# -*- coding: utf-8 -*-
"""EXP-010 — the chain, and the exact line where it stops helping.

Frozen expectations, decided before the run. Note that three of the
eight assert a FAILURE to detect. Those are not gaps discovered by
accident; they are the boundary of the claim, written down so that
nobody — including us — can later describe this ledger as immutable.

  C1 a field edited mid-history      -> ALTERED_ROW at that index
  C2 a row deleted from the middle   -> BROKEN_LINK
  C3 two rows swapped                -> BROKEN_LINK
  C4 a row appended without a hash   -> UNCHAINED_ROW (strict: legacy
     rows are refused, because tolerating them IS the bypass)
  C5 TRUNCATION of the tail          -> chain still verifies. The chain
     proves consistency, never completeness. Caught only by a published
     height.
  C6 a FULL REWRITE by the file's holder -> chain still verifies. Local
     hashes cannot bind a local attacker. Caught only by a published
     root.
  C7 a checkpoint signed by an untrusted key -> refused; and a
     checkpoint whose root does not match the chain at its height ->
     refused. A seal is not trusted for being a seal.
  C8 the honest path verifies clean, and sealing does not break it.
"""
import json

from sanad.chain import (ChainedLedger, GENESIS, compute_hash, seal,
                         verify_against_published, verify_chain,
                         verify_checkpoints)
from sanad.identity import Signer


def build(tmp_path, n=5):
    ledger = ChainedLedger(str(tmp_path / "l.jsonl"))
    for i in range(n):
        ledger.append("execute", "EXECUTED", f"act {i}",
                      amount_minor=1000 + i, execution_id=f"EX-{i}")
    return ledger


def rewrite(ledger, rows):
    with open(ledger.path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- C8
def test_C8_honest_history_verifies_and_sealing_preserves_it(tmp_path):
    ledger = build(tmp_path)
    first = verify_chain(ledger)
    assert first["ok"] and first["height"] == 5

    mohamed = Signer("mohamed")
    cp = seal(ledger, mohamed)

    assert cp["height"] == 5
    assert cp["root"] == first["root"]
    assert verify_chain(ledger)["ok"]              # the seal is itself chained
    assert verify_checkpoints(
        ledger, {"mohamed": mohamed.public_key_b64})["ok"]


# ---------------------------------------------------------------- C1
def test_C1_edited_field_is_caught_and_located(tmp_path):
    ledger = build(tmp_path)
    rows = ledger.rows()
    rows[2]["amount_minor"] = 999999                # the classic quiet edit
    rewrite(ledger, rows)

    result = verify_chain(ledger)

    assert result["ok"] is False
    assert result["problem"] == "ALTERED_ROW"
    assert result["index"] == 2                     # names the row


# ---------------------------------------------------------------- C2
def test_C2_deleted_row_is_caught(tmp_path):
    ledger = build(tmp_path)
    rows = ledger.rows()
    del rows[2]
    rewrite(ledger, rows)

    result = verify_chain(ledger)

    assert result["ok"] is False
    assert result["problem"] == "BROKEN_LINK"
    assert result["index"] == 2


# ---------------------------------------------------------------- C3
def test_C3_reordering_is_caught(tmp_path):
    ledger = build(tmp_path)
    rows = ledger.rows()
    rows[1], rows[3] = rows[3], rows[1]
    rewrite(ledger, rows)

    assert verify_chain(ledger)["problem"] == "BROKEN_LINK"


# ---------------------------------------------------------------- C4
def test_C4_an_unhashed_row_is_refused_not_tolerated(tmp_path):
    ledger = build(tmp_path)
    rows = ledger.rows()
    rows.append({"ts": "2026-01-01T00:00:00+00:00", "stage": "execute",
                 "state": "EXECUTED", "detail": "smuggled",
                 "amount_minor": 500000})           # no hashes at all
    rewrite(ledger, rows)

    result = verify_chain(ledger)

    assert result["problem"] == "UNCHAINED_ROW"
    assert result["index"] == 5
    # the point: leniency here would be the whole bypass — strip the
    # hash and the row walks in.


# ---------------------------------------------------------------- C5
def test_C5_truncation_is_INVISIBLE_to_the_chain(tmp_path):
    """A shorter honest prefix is a perfectly valid chain."""
    ledger = build(tmp_path)
    mohamed = Signer("mohamed")
    seal(ledger, mohamed)
    published = verify_chain(ledger)                # height 6, root R

    rows = ledger.rows()
    rewrite(ledger, rows[:3])                       # cut the tail off

    assert verify_chain(ledger)["ok"] is True       # frozen: NOT caught

    # only an externally held height catches it:
    out = verify_against_published(ledger, published["height"],
                                   published["root"])
    assert out["ok"] is False
    assert out["problem"] == "TRUNCATED"


# ---------------------------------------------------------------- C6
def test_C6_a_full_rewrite_by_the_holder_is_INVISIBLE(tmp_path):
    """Recomputing every hash is trivial for whoever holds the file."""
    ledger = build(tmp_path)
    honest = verify_chain(ledger)

    forged = []
    prev = GENESIS
    for i in range(5):
        row = {"ts": f"2026-01-0{i + 1}T00:00:00+00:00", "stage": "execute",
               "state": "EXECUTED", "detail": "invented",
               "amount_minor": 1, "execution_id": f"EX-{i}",
               "prev_hash": prev}
        row["event_hash"] = compute_hash(row)
        prev = row["event_hash"]
        forged.append(row)
    rewrite(ledger, forged)

    assert verify_chain(ledger)["ok"] is True       # frozen: NOT caught
    assert verify_chain(ledger)["root"] != honest["root"]

    # a root held anywhere the attacker does not control does catch it:
    out = verify_against_published(ledger, honest["height"], honest["root"])
    assert out["ok"] is False
    assert out["problem"] == "DIVERGED_HISTORY"


# ---------------------------------------------------------------- C7
def test_C7_a_seal_is_not_trusted_for_being_a_seal(tmp_path):
    ledger = build(tmp_path)
    mohamed = Signer("mohamed")
    rogue = Signer("rogue")

    seal(ledger, rogue)
    assert verify_checkpoints(
        ledger, {"mohamed": mohamed.public_key_b64}
    )["problem"] == "UNTRUSTED_SEALER"

    # and a trusted sealer whose root was tampered with afterwards:
    ledger2 = ChainedLedger(str(tmp_path / "l2.jsonl"))
    for i in range(3):
        ledger2.append("execute", "EXECUTED", f"act {i}")
    seal(ledger2, mohamed)
    rows = ledger2.rows()
    rows[-1]["root"] = "f" * 64                     # lie about the root
    rows[-1]["signature"] = mohamed.sign(
        f"{rows[-1]['height']}:{rows[-1]['root']}".encode("utf-8"))
    rewrite(ledger2, rows)

    assert verify_checkpoints(
        ledger2, {"mohamed": mohamed.public_key_b64}
    )["problem"] == "SEAL_ROOT_MISMATCH"
