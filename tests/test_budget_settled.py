# -*- coding: utf-8 -*-
"""EXP-012 — finding F3, closed: money the budget could not see.

The live Stripe run exposed it: ledger-derived spend read 7,000 while
Stripe had been charged 8,000. An execution that ended UNKNOWN and was
later settled EXECUTED by reconciliation was recorded as a `resolve`
row — and spent_today_minor() read only execute/EXECUTED rows. Money
left; the budget didn't see it.

The fix, exactly as specified (and independently confirmed):
  - `resolve` rows carry amount_minor,
  - spent_today_minor() derives from execute/EXECUTED + resolve/EXECUTED,
  - no existing row is touched — append-only stays append-only,
  - and nothing is EVER counted twice: reconciliation writes the amount
    only when no execute/EXECUTED row already counted that execution.

Frozen expectations:
  D1 the live-demo scenario, reproduced: 3000 + 4000 executed, 1000
     charged-but-response-lost then settled -> spend reads 8000, equal
     to what actually left
  D2 before settlement the UNKNOWN charge is (honestly) invisible;
     after settlement it is visible — the budget counts what is KNOWN
  D3 the budget now DENIES what the blind budget would have allowed
  D4 NOT_EXECUTED settlement adds nothing — no money moved
  D5 no double counting: an execution with a saved receipt (already an
     execute/EXECUTED row) whose status is re-settled adds ZERO again
  D6 the fix rides the EXP-010 chain untouched: resolve rows with
     amounts still verify
"""
import json

from sanad.chain import ChainedLedger, verify_chain
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


class ChaosProvider:
    def __init__(self):
        self.mode = "ok"
        self.calls = 0
        self.charged_minor = 0
        self.reality = {}

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        receipt = "re_" + execution_id
        self.reality[execution_id] = receipt
        self.charged_minor += amount_minor
        if self.mode == "lost_after":
            raise TimeoutError("charged, response lost")
        return {"receipt": receipt, "amount_minor": amount_minor}

    def find_by_execution_id(self, execution_id):
        r = self.reality.get(execution_id)
        return {"receipt": r, "amount_minor": 0} if r else None

    def retrieve(self, receipt):
        if receipt not in self.reality.values():
            raise LookupError("no such receipt")
        return {"receipt": receipt, "amount_minor": 0}


def build(tmp_path, daily_budget=50000, chained=False):
    clock = FakeClock()
    L = ChainedLedger if chained else Ledger
    ledger = L(str(tmp_path / "l.jsonl"))
    claims = ClaimStore(str(tmp_path / "claims.db"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps(
        {"auto_limit_minor": 5000, "daily_budget_minor": daily_budget,
         "currency": "USD", "blocked_categories": [], "valid_days": 7}),
        encoding="utf-8")
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, str(doc), keys, clock=clock)
    timed_sign(pre, m)
    provider = ChaosProvider()
    gw = TimedGateway(ledger, claims, pre, provider, clock=clock)
    return ledger, claims, gw, provider


# ---------------------------------------------------------------- D1
def test_D1_the_live_demo_numbers_now_agree(tmp_path):
    """The exact scenario that exposed F3, reproduced end to end."""
    ledger, claims, gw, provider = build(tmp_path)
    gw.execute(gw.derive_approval("coffee", 3000, "USD"))
    gw.execute(gw.derive_approval("hotel", 4000, "USD"))

    provider.mode = "lost_after"
    gw.execute(gw.derive_approval("tea", 1000, "USD"))
    provider.mode = "ok"
    recover_on_startup(ledger, claims, provider)

    assert provider.charged_minor == 8000
    assert ledger.spent_today_minor() == 8000          # F3: was 7000


# ---------------------------------------------------------------- D2
def test_D2_unknown_is_invisible_until_it_is_known(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)
    provider.mode = "lost_after"
    gw.execute(gw.derive_approval("coffee", 3000, "USD"))

    assert ledger.spent_today_minor() == 0    # honestly unknown, not hidden
    provider.mode = "ok"
    recover_on_startup(ledger, claims, provider)
    assert ledger.spent_today_minor() == 3000  # known -> counted


# ---------------------------------------------------------------- D3
def test_D3_the_budget_now_denies_what_the_blind_one_allowed(tmp_path):
    ledger, claims, gw, provider = build(tmp_path, daily_budget=8000)
    gw.execute(gw.derive_approval("coffee", 5000, "USD"))
    provider.mode = "lost_after"
    gw.execute(gw.derive_approval("tea", 3000, "USD"))
    provider.mode = "ok"
    recover_on_startup(ledger, claims, provider)       # spend is now 8000

    over = gw.derive_approval("coffee", 1000, "USD")   # blind budget: OK

    assert over is None
    last = [r for r in ledger.rows() if r["stage"] == "approval"][-1]
    assert last["state"] == "DENIED_DAILY_BUDGET"
    assert provider.charged_minor == 8000              # nothing more left


# ---------------------------------------------------------------- D4
def test_D4_not_executed_settlement_adds_nothing(tmp_path):
    ledger, claims, gw, provider = build(tmp_path)

    class LostBefore(ChaosProvider):
        def execute(self, amount_minor, currency, execution_id):
            self.calls += 1
            raise TimeoutError("dropped before the charge")

    gw.provider = LostBefore()
    gw.execute(gw.derive_approval("coffee", 3000, "USD"))
    recover_on_startup(ledger, claims, provider)       # no trace anywhere

    resolve = ledger.last(stage="resolve")
    assert resolve["state"] == "NOT_EXECUTED"
    assert resolve["amount_minor"] == 0
    assert ledger.spent_today_minor() == 0


# ---------------------------------------------------------------- D5
def test_D5_nothing_is_ever_counted_twice(tmp_path):
    """The EXP-007b R7 shape: the money WAS counted by an
    execute/EXECUTED row; only the claim status died. Settlement must
    confirm, not re-count."""
    ledger, claims, gw, provider = build(tmp_path)
    row = gw.execute(gw.derive_approval("coffee", 3000, "USD"))
    assert row["state"] == "EXECUTED"
    assert ledger.spent_today_minor() == 3000

    claims.set_status(row["execution_id"], "UNRESOLVED")   # status died
    settled = recover_on_startup(ledger, claims, provider)

    assert settled == [(row["execution_id"], "EXECUTED")]
    resolve = ledger.last(stage="resolve", state="EXECUTED")
    assert resolve["amount_minor"] == 0                # confirmed, not added
    assert ledger.spent_today_minor() == 3000          # once, still


# ---------------------------------------------------------------- D6
def test_D6_the_fix_rides_the_chain_untouched(tmp_path):
    ledger, claims, gw, provider = build(tmp_path, chained=True)
    provider.mode = "lost_after"
    gw.execute(gw.derive_approval("coffee", 3000, "USD"))
    provider.mode = "ok"
    recover_on_startup(ledger, claims, provider)

    assert ledger.spent_today_minor() == 3000
    assert verify_chain(ledger)["ok"]
