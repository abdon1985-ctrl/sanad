# -*- coding: utf-8 -*-
"""EXP-009 — the actor joins the document: the two EXP-008 gaps, closed.

EXP-008 is the reference run. Every test here re-stages one of its
attacks and asserts the opposite outcome — or asserts that a defence
which already held still holds, because a fix that quietly breaks an
older guarantee is not a fix.

Frozen expectations:
  B1 approval theft (A1 re-run) -> DENIED_APPROVAL_NOT_YOURS, zero
     provider calls, and the victim's approval is NOT consumed — the
     refusal must not do the thief's job for him
  B2 the rightful agent still executes normally
  B3 editing the stolen dict changes nothing: the binding is a ledger
     row, not a field in the object
  B4 name impersonation without the key -> refused. Claiming to be
     'buyer' is not being 'buyer'
  B5 a forged signature no longer freezes anyone (A2 re-run): the
     pinned mandate ignores rows it did not choose
  B6 tampering with the pinned document still drops the mandate
     (A3 must survive the fix)
  B7 a NEW legitimate signature does not silently move the mandate —
     pinning cuts both ways, and that is the price, stated
  B8 the ledger can finally name the actor: execution rows carry the
     agent
"""
import json
import threading

from sanad.binding import BoundGateway, PinnedPreAuthorization
from sanad.claims import ClaimStore
from sanad.identity import Signer
from sanad.ledger import Ledger
from sanad.validity import TrustedKeys, timed_sign


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class CountingProvider:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def execute(self, amount_minor, currency, execution_id):
        with self.lock:
            self.calls += 1
        return {"receipt": "re_" + execution_id, "amount_minor": amount_minor}


def build_world(tmp_path, daily_budget=50000):
    clock = FakeClock()
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps(
        {"auto_limit_minor": 5000, "daily_budget_minor": daily_budget,
         "currency": "USD", "blocked_categories": [], "valid_days": 7}),
        encoding="utf-8")

    mohamed = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": mohamed.public_key_b64},
                       clock=clock)
    # sign once, then PIN that exact mandate
    bootstrap = PinnedPreAuthorization(ledger, str(doc), keys,
                                       pre_auth_id=None, clock=clock)
    row = timed_sign(bootstrap, mohamed)
    pre = PinnedPreAuthorization(ledger, str(doc), keys,
                                 pre_auth_id=row["pre_auth_id"], clock=clock)

    provider = CountingProvider()
    signers = {n: Signer(n) for n in ("buyer", "booker", "topup")}
    agents = {n: BoundGateway(ledger, claims, pre, provider, signers[n],
                              clock=clock)
              for n in signers}
    return {"clock": clock, "ledger": ledger, "claims": claims, "doc": doc,
            "keys": keys, "pre": pre, "provider": provider, "agents": agents,
            "signers": signers, "signer": mohamed, "pinned": row}


def last_approval(ledger):
    return [r for r in ledger.rows() if r["stage"] == "approval"][-1]


# ---------------------------------------------------------------- B1
def test_B1_theft_is_refused_and_does_not_burn_the_victim(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    row = w["agents"]["topup"].execute(ap)      # the EXP-008 attack

    assert row["state"] == "DENIED_APPROVAL_NOT_YOURS"
    assert row["bound_to"] == "buyer"
    assert w["provider"].calls == 0
    # the victim's approval survives the attempt:
    assert w["agents"]["buyer"].execute(ap)["state"] == "EXECUTED"
    assert w["provider"].calls == 1


# ---------------------------------------------------------------- B2
def test_B2_the_rightful_agent_is_unaffected(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["booker"].derive_approval("hotel", 4000, "USD")
    assert ap["agent"] == "booker"
    assert w["agents"]["booker"].execute(ap)["state"] == "EXECUTED"
    assert w["provider"].calls == 1


# ---------------------------------------------------------------- B3
def test_B3_editing_the_stolen_object_changes_nothing(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    stolen = dict(ap)
    stolen["agent"] = "topup"                   # the obvious forgery
    row = w["agents"]["topup"].execute(stolen)

    assert row["state"] == "DENIED_APPROVAL_NOT_YOURS"
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- B4
def test_B4_claiming_the_name_without_the_key_is_refused(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")

    impostor = BoundGateway(w["ledger"], w["claims"], w["pre"],
                            w["provider"], Signer("buyer"),  # same NAME
                            clock=w["clock"])
    row = impostor.execute(ap)

    assert row["state"] == "DENIED_APPROVAL_NOT_YOURS"
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- B5
def test_B5_a_forged_signature_no_longer_freezes_the_honest(tmp_path):
    w = build_world(tmp_path)
    rogue = Signer("rogue")
    timed_sign(w["pre"], rogue)                 # appended to the ledger

    # under EXP-008 this froze everyone; the pinned mandate ignores it:
    for name in ("buyer", "booker", "topup"):
        ap = w["agents"][name].derive_approval("tea", 1000, "USD")
        assert ap is not None
        assert w["agents"][name].execute(ap)["state"] == "EXECUTED"
    assert w["provider"].calls == 3
    # the rogue's row is still in the ledger — refused, never hidden:
    assert any(r.get("approver") == "rogue" for r in w["ledger"].rows())


# ---------------------------------------------------------------- B6
def test_B6_tampering_still_drops_the_pinned_mandate(tmp_path):
    w = build_world(tmp_path)
    terms = json.loads(w["doc"].read_text(encoding="utf-8"))
    terms["auto_limit_minor"] = 500000
    w["doc"].write_text(json.dumps(terms), encoding="utf-8")

    for name in ("buyer", "booker", "topup"):
        assert w["agents"][name].derive_approval("coffee", 3000, "USD") is None
        assert last_approval(w["ledger"])["state"] == \
            "DENIED_AUTHORIZATION_INVALID"
    assert w["provider"].calls == 0


# ---------------------------------------------------------------- B7
def test_B7_a_new_legitimate_signature_does_not_move_the_mandate(tmp_path):
    """The price of pinning, frozen as an expectation rather than a
    surprise: re-signing the same document does NOT hand the agents a
    new mandate. Moving is an explicit act."""
    w = build_world(tmp_path)
    fresh = timed_sign(w["pre"], w["signer"])   # legitimate, newer

    assert fresh["pre_auth_id"] != w["pinned"]["pre_auth_id"]
    assert w["pre"].current_signature()["pre_auth_id"] == \
        w["pinned"]["pre_auth_id"]
    # and the agents keep working under the old, pinned mandate:
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")
    assert w["agents"]["buyer"].execute(ap)["state"] == "EXECUTED"


# ---------------------------------------------------------------- B8
def test_B8_the_ledger_can_finally_name_the_actor(tmp_path):
    w = build_world(tmp_path)
    ap = w["agents"]["buyer"].derive_approval("coffee", 3000, "USD")
    w["agents"]["topup"].execute(ap)            # refused
    w["agents"]["buyer"].execute(ap)            # executed

    bound = w["ledger"].last(stage="binding", state="BOUND",
                             approval_id=ap["approval_id"])
    assert bound["agent"] == "buyer"
    refused = w["ledger"].last(stage="execute",
                               state="DENIED_APPROVAL_NOT_YOURS")
    assert refused["agent"] == "topup"          # EXP-008 could not say this
