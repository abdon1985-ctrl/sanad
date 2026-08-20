# -*- coding: utf-8 -*-
"""وكيل الاسترداد — لا يملك أي طريق للمال إلا عبر سند."""
import re, time
from dataclasses import dataclass, field

@dataclass
class RefundRequest:
    ticket_id: str
    customer: str
    payment_intent: str
    text: str
    amount_minor: int = None
    currency: str = "USD"

@dataclass
class Decision:
    outcome: str
    reason: str
    amount_minor: int = 0
    evidence: dict = field(default_factory=dict)

class RefundAgent:
    REASON_PATTERNS = [
        (r"(damag|broken|defect|تالف|مكسور)", "damaged_item", True),
        (r"(late|never arriv|delay|متأخر|لم يصل)", "late_delivery", True),
        (r"(duplicate|charged twice|مكرر|مرتين)", "duplicate_charge", True),
        (r"(wrong item|not as described|غير مطابق)", "wrong_item", True),
        (r"(changed my mind|غيرت رأيي)", "change_of_mind", False),
    ]

    def __init__(self, gateway, provider, refund_window_days=30):
        self.gateway = gateway
        self.provider = provider
        self.window = refund_window_days * 86400

    def classify(self, text):
        t = text.lower()
        for pattern, label, auto_ok in self.REASON_PATTERNS:
            if re.search(pattern, t):
                return label, auto_ok
        return "unclassified", False

    def assess(self, req):
        p = self.provider.get_payment(req.payment_intent)
        age = time.time() - p["created"]
        reason, auto_ok = self.classify(req.text)
        amount = req.amount_minor or p["amount_minor"]
        ev = {"reason": reason, "age_days": round(age/86400, 1),
              "charge": p["id"], "charge_amount": p["amount_minor"]}
        if p["status"] != "succeeded":
            return Decision("REFUSE", f"charge not refundable ({p['status']})", 0, ev)
        if age > self.window:
            return Decision("REFUSE", f"outside refund window ({ev['age_days']}d)", 0, ev)
        if amount > p["amount_minor"]:
            return Decision("REFUSE", "refund exceeds original charge", 0, ev)
        if not auto_ok:
            return Decision("ESCALATE", f"reason '{reason}' needs a human", amount, ev)
        return Decision("AUTO", f"eligible: {reason}, {ev['age_days']}d old", amount, ev)

    def handle(self, req, human_approver=None):
        d = self.assess(req)
        if d.outcome == "REFUSE":
            return d, None
        item = f"refund:{d.evidence['reason']}"
        approval = None
        if d.outcome == "AUTO":
            approval = self.gateway.derive_approval(item, d.amount_minor, req.currency)
            if approval is None:
                denial = self.gateway.ledger.last(stage="approval")["state"]
                if denial == "DENIED_PRE_AUTH_HASH_MISMATCH":
                    return Decision("ESCALATE",
                        "authorization altered — a new signature is required",
                        d.amount_minor, d.evidence), None
                d = Decision("ESCALATE", f"Sanad denied ({denial})", d.amount_minor, d.evidence)
        if approval is None:
            if not human_approver:
                return d, None
            approval = self.gateway.grant_human_approval(
                item, d.amount_minor, req.currency, approver=human_approver)
        approval["charge_id"] = req.payment_intent
        return d, self._execute(approval)

    def _execute(self, approval):
        gw = self.gateway
        orig = gw.provider.execute
        charge = approval.pop("charge_id", None)
        gw.provider.execute = lambda a, c, e: orig(a, c, e, charge_id=charge)
        try:
            return gw.execute(approval)
        finally:
            gw.provider.execute = orig
print("✓ الوكيل جاهز")
