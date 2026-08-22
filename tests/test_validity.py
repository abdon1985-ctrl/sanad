# -*- coding: utf-8 -*-
"""EXP-006 — time and standing.

Frozen expectations:
  T1 within valid_days                -> (True, 'verified')
  T2 clock past valid_days            -> (False, 'expired...')
  T3 no valid_days in the document    -> never expires (absence = no promise)
  T4 revoke key -> new act denied     -> (False, 'revoked...')
  T5 revocation itself is a ledger row (stage=key, state=REVOKED, reason)
  T6 rows executed BEFORE revocation remain untouched in the ledger
  T7 verify_untampered adapter: same verdicts through the Gateway contract
  T8 valid_days present but signed_at missing -> DENIED (a term is never
     a dead clause: if we cannot prove liveness, we do not assume it)

The clock is a mutable box — tests move time; nothing sleeps.
"""
import json

import pytest

from sanad.ledger import Ledger
from sanad.identity import Signer
from sanad.validity import TrustedKeys, TimedSignedPreAuthorization, timed_sign


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance_days(self, d):
        self.t += d * 86400


@pytest.fixture
def env(tmp_path):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps({
        "auto_limit_minor": 5000, "currency": "USD",
        "valid_days": 7}), encoding="utf-8")
    mohamed = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": mohamed.public_key_b64},
                       clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    row = timed_sign(pre, mohamed)
    return clock, ledger, doc, mohamed, keys, pre, row


def test_T1_within_validity_verifies(env):
    clock, _, _, _, _, pre, row = env
    clock.advance_days(3)
    assert pre.verify_authorization(row) == (True, "verified")


def test_T2_past_validity_expires(env):
    clock, _, _, _, _, pre, row = env
    clock.advance_days(8)
    ok, reason = pre.verify_authorization(row)
    assert ok is False and "expired" in reason


def test_T3_no_valid_days_never_expires(tmp_path):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    doc = tmp_path / "d.json"
    doc.write_text(json.dumps({"auto_limit_minor": 5000,
                               "currency": "USD"}), encoding="utf-8")
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    row = timed_sign(pre, m)
    clock.advance_days(365)
    assert pre.verify_authorization(row) == (True, "verified")


def test_T4_revoked_key_denies_new_acts(env):
    clock, _, _, _, keys, pre, row = env
    assert pre.verify_authorization(row)[0] is True   # alive before
    keys.revoke("mohamed", "device reported stolen")
    ok, reason = pre.verify_authorization(row)
    assert ok is False and "revoked" in reason
    # and it stays denied even inside the validity window
    clock.advance_days(1)
    assert pre.verify_authorization(row)[0] is False


def test_T5_revocation_is_a_ledger_event(env):
    _, ledger, _, _, keys, _, _ = env
    keys.revoke("mohamed", "device reported stolen")
    row = ledger.last(stage="key", state="REVOKED")
    assert row is not None
    assert row["approver"] == "mohamed"
    assert "stolen" in row["reason"]
    assert "revoked_at" in row


def test_T6_history_before_revocation_is_untouched(env):
    _, ledger, _, _, keys, _, _ = env
    ledger.append("execute", "EXECUTED", "receipt=re_demo",
                  execution_id="EX-demo", amount_minor=3000)
    before = [r for r in ledger.rows() if r["state"] == "EXECUTED"]
    keys.revoke("mohamed", "compromise suspected")
    after = [r for r in ledger.rows() if r["state"] == "EXECUTED"]
    assert before == after                     # nothing rewritten
    assert ledger.last(stage="execute", state="EXECUTED") is not None


def test_T7_gateway_adapter_same_verdicts(env):
    clock, _, _, _, keys, pre, row = env
    ok, _ = pre.verify_untampered(row)
    assert ok is True
    clock.advance_days(8)
    ok, detail = pre.verify_untampered(row)
    assert ok is False and "expired" in detail


def test_T8_valid_days_without_signed_at_is_denied(env):
    _, _, _, _, _, pre, row = env
    row = dict(row)
    row.pop("signed_at")
    ok, reason = pre.verify_authorization(row)
    assert ok is False and "signed_at" in reason
