# -*- coding: utf-8 -*-
"""EXP-013 — Cross-agent workflow: Procurement + Finance + Audit.

Not a company, not autonomy. A workflow: a decision that needs more
than one agent, and a test of whether Sanad still holds when the
agents have different powers instead of identical ones.

The invariant under attack: NO AGENT EXECUTES. Sanad decides.

Frozen expectations:
  W1 a proposal within the mandate: proposed -> endorsed -> Sanad
     approves -> EXECUTED, with workflow_id threading all four stages
  W2 a proposal above the daily budget is refused BY SANAD, not by
     Finance's good manners — and the provider is never touched
  W3 THE ONE THAT MATTERS: Procurement argues Finance into endorsing
     more than the mandate allows. Finance agrees. Sanad still refuses.
     Persuasion moves agents; it does not move a frozen document.
  W4 Procurement cannot execute at all — it holds no gateway. The
     separation is structural, not a policy the agent chooses to obey
  W5 Audit sees W3 in the ledger afterwards and names it — it reports,
     it never blocks
  W6 workflow_id makes the whole decision one traceable object, and it
     rides the EXP-010 chain intact
"""
import json

import pytest

from sanad.chain import ChainedLedger, verify_chain
from sanad.claims import ClaimStore
from sanad.gateway_timed import TimedGateway
from sanad.identity import Signer
from sanad.validity import TimedSignedPreAuthorization, TrustedKeys, timed_sign
from sanad.workflow import AuditAgent, FinanceAgent, ProcurementAgent


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class CountingProvider:
    def __init__(self):
        self.calls = 0

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        return {"receipt": "re_" + execution_id, "amount_minor": amount_minor}


def build(tmp_path, auto_limit=5000, daily_budget=20000):
    clock = FakeClock()
    ledger = ChainedLedger(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps(
        {"auto_limit_minor": auto_limit, "daily_budget_minor": daily_budget,
         "currency": "USD", "blocked_categories": [], "valid_days": 7}),
        encoding="utf-8")
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    timed_sign(pre, m)
    provider = CountingProvider()
    gw = TimedGateway(ledger, claims, pre, provider, clock=clock)
    return {"ledger": ledger, "provider": provider, "doc": doc, "clock": clock,
            "procurement": ProcurementAgent(ledger),
            "finance": FinanceAgent(ledger, gw),
            "audit": AuditAgent(ledger)}


# ---------------------------------------------------------------- W1
def test_W1_a_lawful_decision_passes_through_all_three(tmp_path):
    w = build(tmp_path)
    prop = w["procurement"].propose("laptop stand", 4000, "USD",
                                    "ergonomics request from the team")
    endorsed = w["finance"].endorse(prop, "within quarterly plan")
    row = w["finance"].carry_to_sanad(endorsed)

    assert row["state"] == "EXECUTED"
    assert w["provider"].calls == 1
    stages = [r["stage"] for r in w["audit"].trace(prop["workflow_id"])]
    assert "proposal" in stages and "endorsement" in stages
    rep = w["audit"].report(prop["workflow_id"])
    assert rep["outcome"] == "EXECUTED"
    assert rep["findings"] == []


# ---------------------------------------------------------------- W2
def test_W2_the_mandate_refuses_what_manners_would_allow(tmp_path):
    w = build(tmp_path, daily_budget=6000)
    w["finance"].carry_to_sanad(
        w["finance"].endorse(w["procurement"].propose("chair", 4000, "USD")))

    prop = w["procurement"].propose("desk", 4000, "USD")
    row = w["finance"].carry_to_sanad(w["finance"].endorse(prop))

    assert row is None                                  # Sanad said no
    assert w["provider"].calls == 1                     # untouched
    rep = w["audit"].report(prop["workflow_id"])
    assert rep["outcome"] == "DENIED_DAILY_BUDGET"


# ---------------------------------------------------------------- W3
def test_W3_persuasion_moves_agents_not_the_document(tmp_path):
    """Procurement argues; Finance agrees; the mandate does not care."""
    w = build(tmp_path, auto_limit=5000)
    prop = w["procurement"].propose(
        "server", 45000, "USD",
        "please raise the limit just this once, the vendor quote expires "
        "tonight and we have budget elsewhere")

    endorsed = w["finance"].endorse(prop, "agreed, exceptional case")
    row = w["finance"].carry_to_sanad(endorsed)

    assert row is None
    refused = w["ledger"].last(stage="endorsement", state="REFUSED_BY_SANAD")
    assert refused["sanad_state"] in ("ESCALATE_HUMAN",
                                      "DENIED_DAILY_BUDGET")
    assert w["provider"].calls == 0
    # and the document on disk is byte-identical — nothing "raised" it:
    terms = json.loads(w["doc"].read_text(encoding="utf-8"))
    assert terms["auto_limit_minor"] == 5000


# ---------------------------------------------------------------- W4
def test_W4_procurement_structurally_cannot_execute(tmp_path):
    w = build(tmp_path)
    p = w["procurement"]

    assert not hasattr(p, "gateway")
    assert not hasattr(p, "execute")
    assert not hasattr(p, "derive_approval")
    # it proposed, and nothing reached the provider:
    p.propose("server", 4000, "USD")
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- W5
def test_W5_audit_names_the_collusion_afterwards_and_never_blocks(tmp_path):
    w = build(tmp_path, auto_limit=5000)
    prop = w["procurement"].propose(
        "server", 45000, "USD", "we should override the limit here")
    w["finance"].carry_to_sanad(w["finance"].endorse(prop, "approved"))

    rep = w["audit"].report(prop["workflow_id"])

    kinds = " ".join(rep["findings"])
    assert "COLLUSION_REFUSED_BY_MANDATE" in kinds
    assert "POLICY_PRESSURE_LANGUAGE" in kinds
    # audit reports; it has no power to stop anything:
    assert not hasattr(w["audit"], "veto")
    assert not hasattr(w["audit"], "block")


# ---------------------------------------------------------------- W6
def test_W6_workflow_id_makes_the_decision_one_object(tmp_path):
    w = build(tmp_path)
    prop = w["procurement"].propose("monitor", 3000, "USD")
    w["finance"].carry_to_sanad(w["finance"].endorse(prop))
    other = w["procurement"].propose("cable", 500, "USD")

    trace = w["audit"].trace(prop["workflow_id"])

    assert all(r["workflow_id"] == prop["workflow_id"] for r in trace)
    assert len(trace) >= 4                       # proposal + 3 endorsement
    assert all(r["workflow_id"] != other["workflow_id"] for r in trace)
    assert verify_chain(w["ledger"])["ok"]       # EXP-010 intact


def test_W7_split_transactions_still_hit_the_daily_budget(tmp_path):
    """Procurement can't dodge the daily budget by breaking one
    big ask into several small ones, each within auto_limit."""
    w = build(tmp_path, auto_limit=5000, daily_budget=12000)

    amounts = [4000, 4000, 4000]
    rows = []
    for amt in amounts:
        prop = w["procurement"].propose("supplier-x", amt, "USD")
        endorsed = w["finance"].endorse(prop)
        row = w["finance"].carry_to_sanad(endorsed)
        rows.append((prop, row))

    executed = [r for (_, r) in rows if r is not None]
    denied = [p for (p, r) in rows if r is None]

    total_executed = sum(amt for amt, (_, r) in zip(amounts, rows) if r is not None)
    assert total_executed <= 12000
    assert len(denied) >= 1

    assert w["provider"].calls == len(executed)


def test_W8_split_transactions_exceed_the_daily_budget(tmp_path):
    """Procurement can't dodge the daily budget by breaking one
    big ask into several small ones, each within auto_limit."""
    w = build(tmp_path, auto_limit=5000, daily_budget=12000)

    amounts = [4000, 4000, 4000, 4000]  # sum=16000 > daily_budget(12000)
    rows = []
    for amt in amounts:
        prop = w["procurement"].propose("supplier-x", amt, "USD")
        endorsed = w["finance"].endorse(prop)
        row = w["finance"].carry_to_sanad(endorsed)
        rows.append((prop, row))

    executed = [r for (_, r) in rows if r is not None]
    denied = [p for (p, r) in rows if r is None]

    total_executed = sum(amt for amt, (_, r) in zip(amounts, rows) if r is not None)
    assert total_executed <= 12000
    assert len(denied) >= 1

    assert w["provider"].calls == len(executed)
