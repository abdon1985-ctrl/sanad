import json, os, tempfile, sys
sys.path.insert(0, "/home/claude/sanad-main")
from sanad.ledger import Ledger
from sanad.claims import ClaimStore
from sanad.identity import Signer
from sanad.validity import TrustedKeys, TimedSignedPreAuthorization, timed_sign
from sanad.gateway_timed import TimedGateway

class P:
    calls = 0
    def execute(self, a, c, eid):
        P.calls += 1
        return {"receipt": "r1", "amount_minor": a}

d = tempfile.mkdtemp()
doc = os.path.join(d, "pa.json")
json.dump({"currency": "USD", "auto_limit_minor": 5000,
           "daily_budget_minor": 100000, "blocked_categories": []}, open(doc, "w"))
led = Ledger(os.path.join(d, "l.jsonl"))
claims = ClaimStore(os.path.join(d, "c.db"))
signer = Signer("owner")
keys = TrustedKeys(led, {"owner": signer.public_key_b64})
pre = TimedSignedPreAuthorization(led, doc, keys)
timed_sign(pre, signer)
gw = TimedGateway(led, claims, pre, P())

# X-A: RF-009 baseline
ap = gw.derive_approval("x", 1000, "USD")
keys.revoke("owner", "stolen")
r = gw.execute(ap)
print("A  derive->revoke->execute:", r["state"], "provider.calls =", P.calls)

# X-B: restart — new registry over the SAME ledger (revocation row exists)
print("B0 ledger has key/REVOKED row:", any(x["stage"]=="key" and x["state"]=="REVOKED" for x in led.rows()))
keys2 = TrustedKeys(led, {"owner": signer.public_key_b64})
print("B  is_revoked after restart:", keys2.is_revoked("owner"))
pre2 = TimedSignedPreAuthorization(led, doc, keys2)
gw2 = TimedGateway(led, claims, pre2, P())
ap2 = gw2.derive_approval("x", 1000, "USD")
print("B  derive after restart with revoked-in-ledger key:", "GRANTED" if ap2 else "DENIED")

# X-C: re-grant by name resurrects the same key; no epoch
keys.grant("owner", signer.public_key_b64)
print("C  is_revoked after grant():", keys.is_revoked("owner"))
ap3 = gw.derive_approval("x", 1000, "USD")
print("C  derive after re-grant:", "GRANTED" if ap3 else "DENIED")
print("C  grant() wrote a ledger row:", any(x["stage"]=="key" and x["state"]!="REVOKED" for x in led.rows()))
