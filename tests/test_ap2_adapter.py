# -*- coding: utf-8 -*-
"""EXP-011 — the narrow question, answered: can Sanad take an AP2
authorization, bind it to an agent, apply policy, and settle silence?

The mandates here follow the real shapes in google-agentic-commerce/AP2
(models/mandate.py): CartMandate{contents{payment_request, cart_expiry,
merchant_name}, merchant_authorization} and PaymentMandate{
payment_mandate_contents{payment_details_total, ...}, user_authorization}.

Frozen expectations:
  P1 a well-formed mandate pair anchors, derives, executes -> EXECUTED,
     with the anchor row fixing cart/payment/authorization hashes and a
     binding row naming the agent — the two things AP2 itself does not
     record
  P2 no user_authorization -> DENIED_AP2_UNAUTHORIZED, nothing derived
  P3 cart total != payment-mandate total -> DENIED_AP2_TOTAL_MISMATCH
  P4 an AP2 mandate is a cart, not a ceiling: a SMALLER amount is
     refused exactly like a larger one
  P5 expired cart -> refused before the claim, zero provider calls
  P6 EXP-009 carries over: another agent executing the AP2-derived
     approval is refused, and the approval survives for its owner
  P7 EXP-007b carries over: silence under an AP2 mandate settles from
     reality, zero recharges
  P8 GAP, frozen on purpose: a GARBAGE user_authorization string
     anchors successfully. Sanad does not verify AP2 credentials — it
     anchors what was presented. Anyone describing this adapter as
     "verifying AP2" should be pointed at this test.
"""
import json

import pytest

from sanad.ap2_adapter import Ap2Anchor, Ap2BoundGateway
from sanad.chain import ChainedLedger, verify_chain
from sanad.claims import ClaimStore
from sanad.identity import Signer
from sanad.reconcile import recover_on_startup


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class ChaosProvider:
    def __init__(self):
        self.mode = "ok"
        self.calls = 0
        self.reality = {}

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        receipt = "re_" + execution_id
        self.reality[execution_id] = receipt
        if self.mode == "lost_after":
            raise TimeoutError("charged, response lost")
        return {"receipt": receipt, "amount_minor": amount_minor}

    def find_by_execution_id(self, execution_id):
        r = self.reality.get(execution_id)
        return {"receipt": r, "amount_minor": 0} if r else None

    def retrieve(self, receipt):
        return {"receipt": receipt, "amount_minor": 0}


def mandates(total=30.00, currency="USD", expiry="2026-12-31T00:00:00+00:00",
             user_authorization="eyJ.fake-but-present.vc"):
    amount = {"currency": currency, "value": total}
    cart = {
        "contents": {
            "id": "cart_123",
            "user_cart_confirmation_required": False,
            "payment_request": {
                "details": {"id": "pd_123",
                            "total": {"label": "Total", "amount": amount}},
            },
            "cart_expiry": expiry,
            "merchant_name": "CoffeeCo",
        },
        "merchant_authorization": "eyJ.merchant.jwt",
    }
    payment = {
        "payment_mandate_contents": {
            "payment_mandate_id": "pm_123",
            "payment_details_id": "pd_123",
            "payment_details_total": {"label": "Total", "amount": dict(amount)},
            "payment_response": {"request_id": "pd_123",
                                 "method_name": "card"},
            "merchant_agent": "CoffeeCo",
            "timestamp": "2026-08-01T00:00:00+00:00",
        },
        "user_authorization": user_authorization,
    }
    return cart, payment


def build(tmp_path, cart, payment, daily_budget=50000):
    clock = FakeClock()
    ledger = ChainedLedger(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    pre = Ap2Anchor(ledger, cart, payment,
                    daily_budget_minor=daily_budget, clock=clock)
    provider = ChaosProvider()
    agents = {n: Ap2BoundGateway(ledger, claims, pre, provider, Signer(n),
                                 clock=clock)
              for n in ("buyer", "topup")}
    return clock, ledger, claims, pre, provider, agents


# ---------------------------------------------------------------- P1
def test_P1_ap2_mandate_flows_through_the_full_sanad_path(tmp_path):
    cart, payment = mandates()
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)

    assert pre.anchor is not None
    ap = ag["buyer"].derive_approval("coffee", 3000, "USD")
    row = ag["buyer"].execute(ap)

    assert row["state"] == "EXECUTED"
    assert provider.calls == 1
    # the anchor fixed what AP2 presented:
    a = ledger.last(stage="ap2_anchor", state="ANCHORED")
    assert a["payment_mandate_id"] == "pm_123"
    for k in ("cart_hash", "payment_mandate_hash",
              "user_authorization_hash"):
        assert len(a[k]) == 64
    # and Sanad recorded what AP2 cannot: WHO acted
    bound = ledger.last(stage="binding", state="BOUND",
                        approval_id=ap["approval_id"])
    assert bound["agent"] == "buyer"
    # all of it on the hash chain from EXP-010:
    assert verify_chain(ledger)["ok"]


# ---------------------------------------------------------------- P2
def test_P2_missing_user_authorization_is_refused(tmp_path):
    cart, payment = mandates(user_authorization=None)
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)

    assert pre.anchor is None
    assert ledger.last(stage="ap2_anchor")["state"] == \
        "DENIED_AP2_UNAUTHORIZED"
    assert ag["buyer"].derive_approval("coffee", 3000, "USD") is None
    assert provider.calls == 0


# ---------------------------------------------------------------- P3
def test_P3_disagreeing_totals_are_refused_at_the_anchor(tmp_path):
    cart, payment = mandates()
    payment["payment_mandate_contents"]["payment_details_total"] \
        ["amount"]["value"] = 45.00                # the halves disagree
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)

    assert pre.anchor is None
    assert ledger.last(stage="ap2_anchor")["state"] == \
        "DENIED_AP2_TOTAL_MISMATCH"


# ---------------------------------------------------------------- P4
def test_P4_a_mandate_is_a_cart_not_a_ceiling(tmp_path):
    cart, payment = mandates(total=30.00)
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)

    assert ag["buyer"].derive_approval("coffee", 2000, "USD") is None  # under!
    assert ledger.last(stage="approval")["state"] == \
        "DENIED_AP2_AMOUNT_MISMATCH"
    assert ag["buyer"].derive_approval("coffee", 9000, "USD") is None  # over
    assert provider.calls == 0
    # the exact amount still works:
    ap = ag["buyer"].derive_approval("coffee", 3000, "USD")
    assert ag["buyer"].execute(ap)["state"] == "EXECUTED"


# ---------------------------------------------------------------- P5
def test_P5_expired_cart_is_refused_before_any_claim(tmp_path):
    cart, payment = mandates(expiry="2026-01-01T00:00:00+00:00")
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)
    clock.t = 1_800_000_000.0                      # well past expiry

    assert pre.anchor is not None                  # anchored while valid?
    # anchor happened at t=1e6 (before expiry in epoch terms) — the
    # derive-time check is the one that must hold:
    assert ag["buyer"].derive_approval("coffee", 3000, "USD") is None
    assert ledger.last(stage="approval")["state"] == "DENIED_AP2_EXPIRED"
    assert provider.calls == 0


# ---------------------------------------------------------------- P6
def test_P6_binding_carries_over_theft_still_refused(tmp_path):
    cart, payment = mandates()
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)
    ap = ag["buyer"].derive_approval("coffee", 3000, "USD")

    stolen = ag["topup"].execute(ap)

    assert stolen["state"] == "DENIED_APPROVAL_NOT_YOURS"
    assert provider.calls == 0
    assert ag["buyer"].execute(ap)["state"] == "EXECUTED"


# ---------------------------------------------------------------- P7
def test_P7_silence_under_ap2_settles_from_reality(tmp_path):
    cart, payment = mandates()
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)
    provider.mode = "lost_after"
    ap = ag["buyer"].derive_approval("coffee", 3000, "USD")
    row = ag["buyer"].execute(ap)
    assert row["state"] == "UNKNOWN"

    provider.mode = "ok"
    settled = recover_on_startup(ledger, claims, provider)

    assert [s for _, s in settled] == ["EXECUTED"]
    assert provider.calls == 1                     # settled, not recharged


# ---------------------------------------------------------------- P8
def test_P8_GAP_sanad_anchors_credentials_it_does_not_verify(tmp_path):
    """Frozen on purpose. A garbage authorization string anchors fine:
    Sanad fixes WHAT was presented; whether it was genuine is the
    credential provider's question, and pretending otherwise here
    would be verification theater."""
    cart, payment = mandates(user_authorization="THIS-IS-NOT-A-REAL-VC")
    clock, ledger, claims, pre, provider, ag = build(tmp_path, cart, payment)

    assert pre.anchor is not None                  # anchored — frozen
    ap = ag["buyer"].derive_approval("coffee", 3000, "USD")
    assert ag["buyer"].execute(ap)["state"] == "EXECUTED"
    # but the exact bytes relied upon are fixed in the chain:
    import hashlib
    a = ledger.last(stage="ap2_anchor", state="ANCHORED")
    assert a["user_authorization_hash"] == hashlib.sha256(
        b"THIS-IS-NOT-A-REAL-VC").hexdigest()
