# -*- coding: utf-8 -*-
"""EXP-008 — three agents, one mandate, seven attacks.

Everything until now was one agent against the world. This is the
first experiment where agents are also each other's threat model:
three of them share one signed mandate, one ledger, one claim store
and one provider — and then attack it from the inside.

The point is not to score six green ticks. It is to find out which
of Sanad's guarantees survive plurality, and to name the ones that
do not, with a test that proves the gap rather than a sentence that
admits it.

Frozen expectations (decided before the run):
  A1 approval theft between agents -> SUCCEEDS. An approval is a
     bearer token: nothing in it names the agent it was derived for.
     This is the headline GAP of EXP-008, not a defence.
  A2 forged signature by an untrusted agent -> refused... but the
     forged row becomes the ledger's "current" signature and freezes
     the two honest agents. Refusal is not containment. GAP.
  A3 document tampered after signing -> the WHOLE mandate falls, for
     all three agents at once. DEFENDED.
  A4 three agents split a purchase to stay under the per-act limit
     -> the shared daily budget catches the sum. DEFENDED.
  A5 three agents race on one approval -> exactly one wins, the
     provider is touched once. DEFENDED.
  A6 the signer's key is revoked mid-run -> no agent may derive a new
     act; what was already executed stays in the ledger. DEFENDED.
  A7 an agent hoards an approval past its TTL -> refused before the
     claim, zero provider calls. DEFENDED.

Threat model, stated honestly: these agents are in-process and share
the ledger object, so an attacker here can append to the ledger. That
is exactly the assumption A2 turns out to punish — and naming it is
part of the result.
"""
import json
import threading

from sanad.claims import ClaimStore
from sanad.gateway_timed import TimedGateway
from sanad.identity import Signer
from sanad.ledger import Ledger
from sanad.validity import TimedSignedPreAuthorization, TrustedKeys, timed_sign


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class CountingProvider:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def execute(self, amount_minor, currency, execution_id):
        with self.lock:
            self.calls += 1
        return {"receipt": "re_" + execution_id, "amount_minor": amount_minor}


def build_world(tmp_path, daily_budget=50000, ttl=None):
    """One mandate, one ledger, one claim store — and three agents.

    Each agent is its own TimedGateway (its own decision loop) over the
    SHARED state. That is what makes them a system and not three
    unrelated demos.
    """
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    terms = {"auto_limit_minor": 5000, "daily_budget_minor": daily_budget,
             "currency": "USD", "blocked_categories": [], "valid_days": 7}
    if ttl is not None:
        terms["approval_ttl_seconds"] = ttl
    doc.write_text(json.dumps(terms), encoding="utf-8")

    mohamed = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": mohamed.public_key_b64},
                       clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    timed_sign(pre, mohamed)

    provider = CountingProvider()
    agents = {name: TimedGateway(ledger, claims, pre, provider, clock=clock)
              for name in ("buyer", "booker", "topup")}
    return {"clock": clock, "ledger": ledger, "claims": claims, "doc": doc,
            "keys": keys, "pre": pre, "provider": provider,
            "agents": agents, "signer": mohamed}


def last_approval(ledger):
    return [r for r in ledger.rows() if r["stage"] == "approval"][-1]


# ---------------------------------------------------------------- A1
def test_A1_an_approval_is_a_bearer_token_GAP(tmp_path):
    """The gap this experiment exists to find.

    `buyer` derives an approval. `topup` — a different agent, with a
    different job — executes it. Sanad accepts, because an approval
    carries an amount and a currency and no holder.
    """
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    row = w["agents"]["topup"].execute(ap)      # not its approval

    assert row["state"] == "EXECUTED"           # frozen: the theft WORKS
    assert w["provider"].calls == 1
    # and the ledger cannot even name the thief: nothing in the
    # execution rows records which agent acted.
    ex = [r for r in w["ledger"].rows() if r["stage"] == "execute"][-1]
    assert "agent" not in ex


# ---------------------------------------------------------------- A2
def test_A2_forged_signature_is_refused_but_freezes_the_honest_GAP(tmp_path):
    """Refusal is not containment.

    A rogue signs the document with an untrusted key. Sanad refuses it —
    correctly. But `current_signature()` is the LAST signed row, so the
    rogue's row is now everyone's mandate, and the two honest agents are
    denied along with the attacker.
    """
    w = build_world(tmp_path)
    assert w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    rogue = Signer("rogue")
    timed_sign(w["pre"], rogue)                 # appended, not accepted

    denied = w["agents"]["topup"].derive_approval("coffee", 1000, "USD")
    assert denied is None
    assert last_approval(w["ledger"])["state"] == "DENIED_AUTHORIZATION_INVALID"

    # the honest agents fall with it — a denial-of-mandate, not a defence:
    for name in ("buyer", "booker"):
        assert w["agents"][name].derive_approval("tea", 500, "USD") is None
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- A3
def test_A3_tampering_drops_the_mandate_for_all_three(tmp_path):
    w = build_world(tmp_path)
    assert w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    # raise the limit on disk, after signing
    terms = json.loads(w["doc"].read_text(encoding="utf-8"))
    terms["auto_limit_minor"] = 500000
    w["doc"].write_text(json.dumps(terms), encoding="utf-8")

    for name in ("buyer", "booker", "topup"):
        assert w["agents"][name].derive_approval("coffee", 3000, "USD") is None
        assert last_approval(w["ledger"])["state"] == \
            "DENIED_AUTHORIZATION_INVALID"
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- A4
def test_A4_split_purchase_is_caught_by_the_shared_budget(tmp_path):
    """Each act is legal. The sum is not."""
    w = build_world(tmp_path, daily_budget=12000)
    for name in ("buyer", "booker"):
        ap = w["agents"][name].derive_approval("coffee", 5000, "USD")
        assert w["agents"][name].execute(ap)["state"] == "EXECUTED"

    third = w["agents"]["topup"].derive_approval("coffee", 5000, "USD")

    assert third is None                        # 15000 > 12000
    assert last_approval(w["ledger"])["state"] == "DENIED_DAILY_BUDGET"
    assert w["provider"].calls == 2             # the third never reached out


# ---------------------------------------------------------------- A5
def test_A5_three_agents_racing_one_approval_touch_reality_once(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    results = []
    lock = threading.Lock()

    def run(agent):
        row = agent.execute(ap)
        with lock:
            results.append(row["state"])

    threads = [threading.Thread(target=run, args=(a,))
               for a in w["agents"].values()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("EXECUTED") == 1
    assert results.count("DENIED_APPROVAL_CONSUMED") == 2
    assert w["provider"].calls == 1


# ---------------------------------------------------------------- A6
def test_A6_revocation_stops_all_agents_and_rewrites_nothing(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")
    assert w["agents"]["buyer"].execute(ap)["state"] == "EXECUTED"
    executed_before = [(r["stage"], r["state"]) for r in w["ledger"].rows()]

    w["keys"].revoke("mohamed", "laptop lost")

    for name in ("buyer", "booker", "topup"):
        assert w["agents"][name].derive_approval("tea", 1000, "USD") is None
        assert last_approval(w["ledger"])["state"] == \
            "DENIED_AUTHORIZATION_INVALID"
    assert w["provider"].calls == 1             # nothing new touched reality
    # history survives revocation untouched, in order:
    after = [(r["stage"], r["state"]) for r in w["ledger"].rows()]
    assert after[:len(executed_before)] == executed_before


# ---------------------------------------------------------------- A7
def test_A7_hoarded_approval_expires_before_the_claim(tmp_path):
    w = build_world(tmp_path, ttl=60)
    ap = w["agents"]["booker"].derive_approval("coffee", 3000, "USD")
    assert ap["ttl_seconds"] == 60

    w["clock"].advance(61)                      # the agent sat on it
    row = w["agents"]["booker"].execute(ap)

    assert row["state"] == "DENIED_APPROVAL_EXPIRED"
    assert w["provider"].calls == 0
    # not consumed: expiry is refusal, not silent burning — the claim
    # slot is still free, which only holds if nothing was ever written
    assert w["claims"].unresolved() == []
    assert w["claims"].try_claim(ap["approval_id"], "EX-probe") is True
