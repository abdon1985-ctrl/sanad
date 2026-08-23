# -*- coding: utf-8 -*-
"""EXP-007b — reconciliation under chaos: reality answers the silence.

EXP-007a proved that silence never becomes success. It left UNKNOWN
standing there, unsettled, on purpose. This half proves the other side
of the same claim: that the unsettled state is *settled by asking the
provider*, never by guessing and never by retrying.

Frozen expectations:
  R1 money DID move, response lost -> reconcile finds it at the provider
     -> EXECUTED (found by search), zero new charges
  R2 money did NOT move -> no trace at the provider -> NOT_EXECUTED,
     the honest verdict that makes a NEW approval safe
  R3 the provider is down DURING settlement -> UNRESOLVED, and the very
     next run settles it — "re-enters next run" becomes proven, not
     documented
  R4 a mid-flight crash (no execute row at all) is still settled from
     reality — the ledger gap is not a blind spot
  R5 reconciliation NEVER charges: the execute counter is identical
     across any number of runs — the categorical difference between
     "ask reality" and "retry"
  R6 a settled execution leaves the queue: the second run has nothing
     to do (settlement is terminal, PENDING/UNRESOLVED is not)
  R7 when the receipt was saved, settlement uses direct retrieve and
     never touches the lagging search index

No new library code. One test file pressing on what already exists.
"""
import json

import pytest

from sanad.claims import ClaimStore
from sanad.gateway_timed import TimedGateway
from sanad.identity import Signer
from sanad.ledger import Ledger
from sanad.reconcile import recover_on_startup
from sanad.validity import TimedSignedPreAuthorization, TrustedKeys, timed_sign


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class MidFlightCrash(BaseException):
    """Process death, not an error — the Gateway must not swallow it."""


class ChaosProvider:
    """Two independent dials:

    mode        — how execute() fails: ok | lost_after | lost_before | crash
    settle_mode — how the provider answers reconcile: ok | down

    `reality` is what the outside world actually did, regardless of what
    came back over the wire. It is the ground truth the test asserts
    against — and the only thing reconcile is allowed to consult.
    """

    def __init__(self):
        self.mode = "ok"
        self.settle_mode = "ok"
        self.calls = 0            # execute() calls — money-touching
        self.searches = 0         # find_by_execution_id() calls
        self.retrieves = 0        # retrieve() calls
        self.reality = {}         # execution_id -> receipt

    # ---- the money-touching path ----
    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        if self.mode == "lost_before":
            raise TimeoutError("connection dropped before the charge")
        if self.mode == "crash":
            raise MidFlightCrash()
        receipt = "re_chaos_" + execution_id
        self.reality[execution_id] = receipt        # reality happens here
        if self.mode == "lost_after":
            raise TimeoutError("charge went through, response lost")
        return {"receipt": receipt, "amount_minor": amount_minor}

    # ---- the read-only settlement path ----
    def find_by_execution_id(self, execution_id):
        self.searches += 1
        if self.settle_mode == "down":
            raise ConnectionError("provider unreachable during settlement")
        receipt = self.reality.get(execution_id)
        return {"receipt": receipt, "amount_minor": 0} if receipt else None

    def retrieve(self, receipt):
        self.retrieves += 1
        if self.settle_mode == "down":
            raise ConnectionError("provider unreachable during settlement")
        if receipt not in self.reality.values():
            raise LookupError("no such receipt")
        return {"receipt": receipt, "amount_minor": 0}


def build(tmp_path):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps(
        {"auto_limit_minor": 5000, "daily_budget_minor": 50000,
         "currency": "USD", "blocked_categories": [],
         "valid_days": 7}), encoding="utf-8")
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    timed_sign(pre, m)
    provider = ChaosProvider()
    gw = TimedGateway(ledger, claims, pre, provider, clock=clock)
    return ledger, claims, gw, provider


def unknown_execution(gw, provider, mode="lost_after", item="coffee"):
    """Drive one execution into silence and hand back its execution_id."""
    provider.mode = mode
    ap = gw.derive_approval(item, 3000, "USD")
    row = gw.execute(ap)
    return ap, row


def test_R1_executed_in_reality_is_found_and_settled(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    ap, row = unknown_execution(gw, provider, "lost_after")
    ex_id = row["execution_id"]
    assert row["state"] == "UNKNOWN"
    assert claims.status_of(ex_id) == "PENDING"

    settled = recover_on_startup(ledger, claims, provider)

    assert settled == [(ex_id, "EXECUTED")]
    assert claims.status_of(ex_id) == "EXECUTED"
    resolve = ledger.last(stage="resolve", execution_id=ex_id)
    assert resolve["state"] == "EXECUTED"
    assert "found by search" in resolve["detail"]
    assert provider.calls == 1            # settlement charged nothing


def test_R2_not_executed_is_the_honest_verdict(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    ap, row = unknown_execution(gw, provider, "lost_before")
    ex_id = row["execution_id"]
    assert row["state"] == "UNKNOWN"
    assert provider.reality == {}          # nothing happened out there

    settled = recover_on_startup(ledger, claims, provider)

    assert settled == [(ex_id, "NOT_EXECUTED")]
    assert claims.status_of(ex_id) == "NOT_EXECUTED"
    assert "safe to retry" in ledger.last(stage="resolve",
                                          execution_id=ex_id)["detail"]


def test_R3_unresolved_re_enters_and_settles_next_run(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    ap, row = unknown_execution(gw, provider, "lost_after")
    ex_id = row["execution_id"]

    provider.settle_mode = "down"          # the provider is unreachable
    first = recover_on_startup(ledger, claims, provider)
    assert first == [(ex_id, "UNRESOLVED")]
    assert claims.status_of(ex_id) == "UNRESOLVED"
    assert claims.unresolved() != []       # still in the queue

    provider.settle_mode = "ok"            # reality answers this time
    second = recover_on_startup(ledger, claims, provider)
    assert second == [(ex_id, "EXECUTED")]
    assert claims.status_of(ex_id) == "EXECUTED"


def test_R4_mid_flight_crash_is_settled_from_reality(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    provider.mode = "crash"
    ap = gw.derive_approval("coffee", 3000, "USD")
    with pytest.raises(MidFlightCrash):
        gw.execute(ap)                     # dies with no execute row

    assert [r for r in ledger.rows() if r["stage"] == "execute"] == []
    pending = claims.unresolved()
    assert len(pending) == 1               # the claim survived the death

    settled = recover_on_startup(ledger, claims, provider)

    assert [s for _, s in settled] == ["NOT_EXECUTED"]
    assert provider.calls == 1             # the one dead call, no more


def test_R5_reconciliation_never_charges(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    ap, row = unknown_execution(gw, provider, "lost_after")
    charges_before = provider.calls

    for _ in range(5):
        recover_on_startup(ledger, claims, provider)

    assert provider.calls == charges_before   # identical, not "close enough"
    # and settlement never gives the burned approval back:
    assert gw.execute(ap)["state"] == "DENIED_APPROVAL_CONSUMED"
    assert provider.calls == charges_before


def test_R6_settled_executions_leave_the_queue(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    unknown_execution(gw, provider, "lost_after")

    assert recover_on_startup(ledger, claims, provider) != []
    assert claims.unresolved() == []
    assert recover_on_startup(ledger, claims, provider) == []   # nothing left
    searches_after = provider.searches
    recover_on_startup(ledger, claims, provider)
    assert provider.searches == searches_after   # not even a question asked


def test_R7_saved_receipt_settles_by_direct_retrieve(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    provider.mode = "ok"
    ap = gw.derive_approval("tea", 2000, "USD")
    row = gw.execute(ap)                  # EXECUTED, receipt saved
    ex_id = row["execution_id"]
    assert row["state"] == "EXECUTED"

    # the status update died after the ledger line was written:
    claims.set_status(ex_id, "UNRESOLVED")
    searches_before = provider.searches

    settled = recover_on_startup(ledger, claims, provider)

    assert settled == [(ex_id, "EXECUTED")]
    assert provider.retrieves == 1                    # receipt first
    assert provider.searches == searches_before       # index never touched
    assert "direct retrieve" in ledger.last(stage="resolve",
                                            execution_id=ex_id)["detail"]
