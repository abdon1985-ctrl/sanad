# -*- coding: utf-8 -*-
"""EXP-003 as automated tests: atomic claim, single provider call, recovery.

These tests prove GATEWAY logic with an injected in-memory provider that
counts real calls made to it. Provider-side reality (Stripe) was proven
separately in EXP-003b/EXP-004 runs against test mode.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from sanad import Ledger, ClaimStore, Gateway, PreAuthorization, recover_on_startup
from sanad.providers import Provider, ProviderRejected


class CountingProvider(Provider):
    """In-memory world: every execute() is a counted, recorded touch."""
    name = "memory"

    def __init__(self, fail_with=None):
        self.calls = 0
        self.records = {}
        self._lock = threading.Lock()
        self.fail_with = fail_with

    def execute(self, amount_minor, currency, execution_id):
        with self._lock:
            self.calls += 1
        if self.fail_with:
            raise self.fail_with
        receipt = "rcpt_" + execution_id
        self.records[execution_id] = {"receipt": receipt,
                                      "amount_minor": amount_minor}
        return self.records[execution_id]

    def find_by_execution_id(self, execution_id):
        return self.records.get(execution_id)

    def retrieve(self, receipt):
        for r in self.records.values():
            if r["receipt"] == receipt:
                return r
        raise KeyError(receipt)


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text('{"auto_limit_minor": 5000, "daily_budget_minor": 100000,'
                   ' "currency": "USD", "blocked_categories": []}',
                   encoding="utf-8")
    pre = PreAuthorization(ledger, str(doc))
    pre.sign("tester")
    provider = CountingProvider()
    gw = Gateway(ledger, claims, pre, provider)
    return ledger, claims, pre, provider, gw, doc


def test_race_one_claim_one_call(env):
    """10 concurrent workers, one approval -> exactly 1 claim, 1 call, 9 denials."""
    ledger, claims, pre, provider, gw, _ = env
    approval = gw.derive_approval("coffee", 1200, "USD")

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(gw.execute, approval) for _ in range(10)]
        for f in as_completed(futures):
            results.append(f.result()["state"])

    assert results.count("EXECUTED") == 1
    assert results.count("DENIED_APPROVAL_CONSUMED") == 9
    assert provider.calls == 1
    claim_lines = [r for r in ledger.rows() if r["stage"] == "claim"]
    assert len(claim_lines) == 1


def test_race_repeated_rounds(env, tmp_path):
    """20 rounds of the race — the invariant holds every time, not by luck."""
    ledger, claims, pre, provider, gw, _ = env
    for _ in range(20):
        approval = gw.derive_approval("coffee", 100, "USD")
        with ThreadPoolExecutor(max_workers=10) as ex:
            states = [f.result()["state"] for f in
                      as_completed([ex.submit(gw.execute, approval)
                                    for _ in range(10)])]
        assert states.count("EXECUTED") == 1
        assert states.count("DENIED_APPROVAL_CONSUMED") == 9


def test_unknown_stays_pending_then_recovers(env):
    """Lost response -> UNKNOWN -> approval stays burned -> reconcile settles
    from reality (NOT_EXECUTED here: the provider holds no record)."""
    ledger, claims, pre, provider, gw, _ = env
    provider.fail_with = TimeoutError()
    approval = gw.derive_approval("coffee", 1200, "USD")
    result = gw.execute(approval)
    assert result["state"] == "UNKNOWN"

    # the approval is burned: a second execute is denied, zero new calls
    calls_before = provider.calls
    again = gw.execute(approval)
    assert again["state"] == "DENIED_APPROVAL_CONSUMED"
    assert provider.calls == calls_before

    # startup recovery settles from the provider's records (none -> NOT_EXECUTED)
    provider.fail_with = None
    settled = recover_on_startup(ledger, claims, gw.provider)
    assert settled and settled[0][1] == "NOT_EXECUTED"


def test_rejected_is_terminal(env):
    ledger, claims, pre, provider, gw, _ = env
    provider.fail_with = ProviderRejected("HTTP 400")
    approval = gw.derive_approval("coffee", 1200, "USD")
    result = gw.execute(approval)
    assert result["state"] == "REJECTED"
    assert claims.unresolved() == []   # terminal — recovery has nothing to do
