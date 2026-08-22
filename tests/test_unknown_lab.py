# -*- coding: utf-8 -*-
"""EXP-007a — the harsh UNKNOWN lab: silence never becomes success.

A chaos provider fails in every ugly way a real one does. The question
is single: does the ledger stay truthful, and does Sanad refuse to turn
silence into money?

Frozen expectations:
  U1 response lost AFTER the provider executed -> UNKNOWN, 1 provider call
  U2 retry with the same approval -> DENIED_APPROVAL_CONSUMED, still 1 call
     (an UNKNOWN can never become a double-spend through Sanad)
  U3 response lost BEFORE execution -> ledger shape IDENTICAL to U1
     (from inside, silence is silence — which is exactly why settlement
      must come from reality, not from a guess or a retry)
  U4 hard crash mid-flight (after the claim, before the provider
     returns) -> the trace survives: CLAIMED + pre-action SENT, no
     execute row, approval burned — the recovery loop's raw material
  U5 an UNKNOWN never silently mutates: no set_status call, the last
     execute row stays UNKNOWN
  U6 the system is not wedged: a fresh approval executes normally

Chaos is injected, like the clock: tests choose the failure, never
wait for one.
"""
import json

import pytest

from sanad.ledger import Ledger
from sanad.identity import Signer
from sanad.validity import TrustedKeys, TimedSignedPreAuthorization, timed_sign
from sanad.gateway_timed import TimedGateway


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeClaims:
    def __init__(self):
        self.claimed = {}
        self.status = {}

    def try_claim(self, approval_id, execution_id):
        if approval_id in self.claimed:
            return False
        self.claimed[approval_id] = execution_id
        return True

    def set_status(self, execution_id, s):
        self.status[execution_id] = s


class MidFlightCrash(BaseException):
    """A crash the Gateway must NOT swallow — process death, not an error."""


class ChaosProvider:
    """mode: ok | lost_after | lost_before | crash"""

    def __init__(self):
        self.mode = "ok"
        self.calls = 0
        self.executed_for = []       # what REALITY did, regardless of response

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        if self.mode == "lost_before":
            raise TimeoutError("connection dropped before the charge")
        if self.mode == "crash":
            raise MidFlightCrash()
        # reality happens
        self.executed_for.append(execution_id)
        if self.mode == "lost_after":
            raise TimeoutError("charge went through, response lost")
        return {"receipt": "re_chaos_" + execution_id,
                "amount_minor": amount_minor}


def build(tmp_path):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
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
    gw = TimedGateway(ledger, FakeClaims(), pre, provider, clock=clock)
    return ledger, gw, provider


def exec_rows(ledger):
    return [r for r in ledger.rows() if r["stage"] == "execute"]


def test_U1_lost_after_execution_is_UNKNOWN(tmp_path):
    ledger, gw, provider = build(tmp_path)
    provider.mode = "lost_after"
    ap = gw.derive_approval("coffee", 3000, "USD")
    row = gw.execute(ap)
    assert row["state"] == "UNKNOWN"
    assert provider.calls == 1
    assert len(provider.executed_for) == 1     # reality DID happen


def test_U2_retry_cannot_double_spend(tmp_path):
    ledger, gw, provider = build(tmp_path)
    provider.mode = "lost_after"
    ap = gw.derive_approval("coffee", 3000, "USD")
    gw.execute(ap)
    provider.mode = "ok"                        # network healed
    row2 = gw.execute(ap)                       # the tempting retry
    assert row2["state"] == "DENIED_APPROVAL_CONSUMED"
    assert provider.calls == 1                  # reality touched ONCE


def test_U3_silence_is_indistinguishable_from_inside(tmp_path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    ledger_a, gw_a, prov_a = build(a)
    prov_a.mode = "lost_after"
    gw_a.execute(gw_a.derive_approval("coffee", 3000, "USD"))

    ledger_b, gw_b, prov_b = build(b)
    prov_b.mode = "lost_before"
    gw_b.execute(gw_b.derive_approval("coffee", 3000, "USD"))

    shape_a = [(r["stage"], r["state"]) for r in ledger_a.rows()]
    shape_b = [(r["stage"], r["state"]) for r in ledger_b.rows()]
    assert shape_a == shape_b                   # identical from inside
    # ...yet reality differs — the money moved only in A:
    assert len(prov_a.executed_for) == 1
    assert len(prov_b.executed_for) == 0


def test_U4_mid_flight_crash_leaves_a_recoverable_trace(tmp_path):
    ledger, gw, provider = build(tmp_path)
    provider.mode = "crash"
    ap = gw.derive_approval("coffee", 3000, "USD")
    with pytest.raises(MidFlightCrash):
        gw.execute(ap)                          # process "dies" here
    assert exec_rows(ledger) == []              # no execute row at all
    assert ledger.last(stage="claim", state="CLAIMED") is not None
    assert ledger.last(stage="pre-action", state="SENT") is not None
    assert ap["approval_id"] in gw.claims.claimed   # burned before the world


def test_U5_unknown_never_mutates_silently(tmp_path):
    ledger, gw, provider = build(tmp_path)
    provider.mode = "lost_after"
    ap = gw.derive_approval("coffee", 3000, "USD")
    row = gw.execute(ap)
    ex_id = row["execution_id"]
    assert ex_id not in gw.claims.status        # no status written
    assert exec_rows(ledger)[-1]["state"] == "UNKNOWN"


def test_U6_system_not_wedged_after_unknown(tmp_path):
    ledger, gw, provider = build(tmp_path)
    provider.mode = "lost_after"
    gw.execute(gw.derive_approval("coffee", 3000, "USD"))
    provider.mode = "ok"
    ap2 = gw.derive_approval("tea", 2000, "USD")
    row = gw.execute(ap2)
    assert row["state"] == "EXECUTED"
    assert provider.calls == 2
