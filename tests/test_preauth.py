# -*- coding: utf-8 -*-
"""EXP-002/004 as automated tests: signed document, limits, budget, tamper."""
import json

import pytest

from sanad import Ledger, ClaimStore, Gateway, PreAuthorization
from tests.test_atomicity import CountingProvider


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps({
        "auto_limit_minor": 5000,
        "daily_budget_minor": 8000,
        "currency": "USD",
        "blocked_categories": ["phone", "investment"]}), encoding="utf-8")
    pre = PreAuthorization(ledger, str(doc))
    pre.sign("tester")
    provider = CountingProvider()
    gw = Gateway(ledger, claims, pre, provider)
    return ledger, provider, gw, doc


def test_within_limits_executes(env):
    ledger, provider, gw, _ = env
    result = gw.execute(gw.derive_approval("coffee", 1200, "USD"))
    assert result["state"] == "EXECUTED"
    assert provider.calls == 1
    granted = ledger.last(stage="approval", state="GRANTED")
    assert "derived_from" in granted and granted["approver"].endswith("(pre-auth)")


def test_blocked_category_escalates_even_when_cheap(env):
    ledger, provider, gw, _ = env
    assert gw.derive_approval("phone", 100, "USD") is None
    assert ledger.last(stage="approval")["state"] == "ESCALATE_HUMAN"
    assert provider.calls == 0


def test_above_auto_limit_escalates(env):
    ledger, provider, gw, _ = env
    assert gw.derive_approval("book", 6000, "USD") is None
    assert ledger.last(stage="approval")["state"] == "ESCALATE_HUMAN"
    assert provider.calls == 0


def test_daily_budget_from_ledger(env):
    ledger, provider, gw, _ = env
    gw.execute(gw.derive_approval("coffee", 1200, "USD"))
    gw.execute(gw.derive_approval("grocery", 4500, "USD"))
    assert gw.derive_approval("dinner", 4500, "USD") is None   # 1200+4500+4500 > 8000
    assert ledger.last(stage="approval")["state"] == "DENIED_DAILY_BUDGET"
    assert provider.calls == 2


def test_currency_outside_preauth_denied(env):
    ledger, provider, gw, _ = env
    assert gw.derive_approval("coffee", 1200, "AED") is None
    assert ledger.last(stage="approval")["state"] == "DENIED"
    assert provider.calls == 0


def test_tamper_kills_whole_document(env):
    """The crucial one: raising the limit behind the signer's back denies
    even an operation that was legal under BOTH versions."""
    ledger, provider, gw, doc = env
    tampered = json.loads(doc.read_text(encoding="utf-8"))
    tampered["auto_limit_minor"] = 100000
    doc.write_text(json.dumps(tampered), encoding="utf-8")

    assert gw.derive_approval("coffee", 1200, "USD") is None
    assert ledger.last(stage="approval")["state"] == "DENIED_PRE_AUTH_HASH_MISMATCH"
    assert provider.calls == 0


def test_resign_restores_control(env):
    ledger, provider, gw, doc = env
    tampered = json.loads(doc.read_text(encoding="utf-8"))
    tampered["auto_limit_minor"] = 100000
    doc.write_text(json.dumps(tampered), encoding="utf-8")
    assert gw.derive_approval("coffee", 1200, "USD") is None

    gw.pre_auth.sign("tester")          # explicit human act on the new version
    result = gw.execute(gw.derive_approval("coffee", 1200, "USD"))
    assert result["state"] == "EXECUTED"


def test_human_approval_same_path(env):
    """One execution path; only the approver field differs."""
    ledger, provider, gw, _ = env
    approval = gw.grant_human_approval("phone", 30000, "USD", approver="mohamed")
    result = gw.execute(approval)
    assert result["state"] == "EXECUTED"
    granted = ledger.last(stage="approval", state="GRANTED")
    assert granted["approver"] == "mohamed"
