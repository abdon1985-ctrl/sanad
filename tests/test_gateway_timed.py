# -*- coding: utf-8 -*-
"""EXP-006b — the signed path is the live path, and approvals expire.

Frozen expectations:
  W1 signed pre-auth -> derive -> execute -> EXECUTED (crypto path IS the live path)
  W2 revoked key     -> derive None, DENIED_AUTHORIZATION_INVALID ('revoked'), 0 provider calls
  W3 expired document-> derive None, DENIED_AUTHORIZATION_INVALID ('expired'), 0 provider calls
  W4 tampered doc    -> derive None ('signature does not verify'), 0 provider calls
  T1 approval past ttl -> DENIED_APPROVAL_EXPIRED, approval NOT consumed, 0 provider calls
  T2 approval within ttl -> EXECUTED
  T3 no ttl in terms -> approval never expires (absence = no promise)

Fakes implement only the interface the Gateway uses (duck typing) —
no dependence on the real ClaimStore/Provider constructors.
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

    def advance(self, seconds):
        self.t += seconds

    def advance_days(self, d):
        self.t += d * 86400


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


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        return {"receipt": "re_test_" + execution_id,
                "amount_minor": amount_minor}


def build(tmp_path, terms):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps(terms), encoding="utf-8")
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    timed_sign(pre, m)
    provider = FakeProvider()
    gw = TimedGateway(ledger, FakeClaims(), pre, provider, clock=clock)
    return clock, ledger, doc, keys, gw, provider


BASE = {"auto_limit_minor": 5000, "daily_budget_minor": 20000,
        "currency": "USD", "blocked_categories": [],
        "valid_days": 7, "approval_ttl_seconds": 300}


def test_W1_signed_path_is_the_live_path(tmp_path):
    _, ledger, _, _, gw, provider = build(tmp_path, BASE)
    ap = gw.derive_approval("coffee", 3000, "USD")
    assert ap is not None
    row = gw.execute(ap)
    assert row["state"] == "EXECUTED" and provider.calls == 1
    granted = ledger.last(stage="approval", state="GRANTED")
    assert "(pre-auth)" in granted["approver"]


def test_W2_revoked_key_denies_derivation(tmp_path):
    _, ledger, _, keys, gw, provider = build(tmp_path, BASE)
    keys.revoke("mohamed", "device reported stolen")
    ap = gw.derive_approval("coffee", 3000, "USD")
    assert ap is None
    row = ledger.last(stage="approval", state="DENIED_AUTHORIZATION_INVALID")
    assert row is not None and "revoked" in row["detail"]
    assert provider.calls == 0


def test_W3_expired_document_denies_derivation(tmp_path):
    clock, ledger, _, _, gw, provider = build(tmp_path, BASE)
    clock.advance_days(8)
    ap = gw.derive_approval("coffee", 3000, "USD")
    assert ap is None
    row = ledger.last(stage="approval", state="DENIED_AUTHORIZATION_INVALID")
    assert row is not None and "expired" in row["detail"]
    assert provider.calls == 0


def test_W4_tampered_document_denies_derivation(tmp_path):
    _, ledger, doc, _, gw, provider = build(tmp_path, BASE)
    t = json.loads(doc.read_text())
    t["auto_limit_minor"] = 999999
    doc.write_text(json.dumps(t), encoding="utf-8")
    ap = gw.derive_approval("coffee", 3000, "USD")
    assert ap is None
    row = ledger.last(stage="approval", state="DENIED_AUTHORIZATION_INVALID")
    assert row is not None and "does not verify" in row["detail"]
    assert provider.calls == 0


def test_T1_expired_approval_not_consumed_no_provider_call(tmp_path):
    clock, ledger, _, _, gw, provider = build(tmp_path, BASE)
    ap = gw.derive_approval("coffee", 3000, "USD")
    clock.advance(301)
    row = gw.execute(ap)
    assert row["state"] == "DENIED_APPROVAL_EXPIRED"
    assert provider.calls == 0
    assert ap["approval_id"] not in gw.claims.claimed   # not consumed
    assert ledger.last(stage="claim", state="CLAIMED") is None


def test_T2_within_ttl_executes(tmp_path):
    clock, _, _, _, gw, provider = build(tmp_path, BASE)
    ap = gw.derive_approval("coffee", 3000, "USD")
    clock.advance(299)
    row = gw.execute(ap)
    assert row["state"] == "EXECUTED" and provider.calls == 1


def test_T3_no_ttl_never_expires(tmp_path):
    terms = dict(BASE)
    terms.pop("approval_ttl_seconds")
    clock, _, _, _, gw, provider = build(tmp_path, terms)
    ap = gw.derive_approval("coffee", 3000, "USD")
    clock.advance_days(30)
    row = gw.execute(ap)
    assert row["state"] == "EXECUTED" and provider.calls == 1
