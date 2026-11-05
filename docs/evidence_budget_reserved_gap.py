import json, os, sys, tempfile
tree = sys.argv[1]; sys.path.insert(0, tree)
from sanad.ledger import Ledger
from sanad.claims import ClaimStore
from sanad.identity import Signer
from sanad.validity import TrustedKeys, TimedSignedPreAuthorization, timed_sign
from sanad.gateway_timed import TimedGateway

class P:
    calls = 0; total = 0
    def execute(self, a, c, eid):
        P.calls += 1; P.total += a
        return {"receipt": "r%d" % P.calls, "amount_minor": a}

d = tempfile.mkdtemp(); doc = os.path.join(d, "pa.json")
json.dump({"currency": "USD", "auto_limit_minor": 5000,
           "daily_budget_minor": 12000, "blocked_categories": []}, open(doc, "w"))
led = Ledger(os.path.join(d, "l.jsonl")); claims = ClaimStore(os.path.join(d, "c.db"))
signer = Signer("owner")
keys = TrustedKeys(led, {"owner": signer.public_key_b64})
pre = TimedSignedPreAuthorization(led, doc, keys); timed_sign(pre, signer)
gw = TimedGateway(led, claims, pre, P())

apps = [gw.derive_approval("x", 4000, "USD") for _ in range(4)]      # derive first
print("derived (budget 12000, 4 x 4000):", [bool(a) for a in apps])
rows = [gw.execute(a) for a in apps if a]                              # execute after
print("execute states:", [r["state"] for r in rows])
print("provider total:", P.total, "| spent_today_minor():", led.spent_today_minor())
print("exceeds budget:", P.total > 12000)
