# -*- coding: utf-8 -*-
"""مزوّد الاسترداد — Stripe خلف نفس واجهة Provider."""
import json, urllib.error, urllib.parse, urllib.request
from sanad.providers import Provider, ProviderRejected
API = "https://api.stripe.com"

class RefundProvider(Provider):
    name = "stripe-refund"

    def __init__(self, secret_key, timeout=20):
        if not secret_key or not secret_key.startswith("sk_"):
            raise ValueError("sk_ key required")
        self.key = secret_key
        self.timeout = timeout

    def _req(self, path, body=None, idem=None):
        data = urllib.parse.urlencode(body).encode() if body else None
        r = urllib.request.Request(API + path, data=data)
        r.add_header("Authorization", "Bearer " + self.key)
        if idem: r.add_header("Idempotency-Key", idem)
        with urllib.request.urlopen(r, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def execute(self, amount_minor, currency, execution_id, charge_id=None):
        try:
            body = {"amount": amount_minor, "metadata[execution_id]": execution_id}
            if charge_id: body["payment_intent"] = charge_id
            rf = self._req("/v1/refunds", body, idem="sanad-rf-" + execution_id)
            return {"receipt": rf["id"], "amount_minor": rf["amount"]}
        except urllib.error.HTTPError as e:
            raise ProviderRejected(f"HTTP {e.code}") from e

    def retrieve(self, receipt):
        rf = self._req("/v1/refunds/" + receipt)
        return {"receipt": rf["id"], "amount_minor": rf["amount"]}

    def find_by_execution_id(self, eid):
        for rf in self._req("/v1/refunds?limit=100").get("data", []):
            if rf.get("metadata", {}).get("execution_id") == eid:
                return {"receipt": rf["id"], "amount_minor": rf["amount"]}
        return None

    def create_test_charge(self, amount_minor, currency="usd"):
        pi = self._req("/v1/payment_intents", {
            "amount": amount_minor, "currency": currency,
            "payment_method": "pm_card_visa", "confirm": "true",
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never"})
        return pi["id"], pi["amount"], pi["created"]

    def get_payment(self, pid):
        pi = self._req("/v1/payment_intents/" + pid)
        return {"id": pi["id"], "amount_minor": pi["amount"],
                "currency": pi["currency"].upper(),
                "created": pi["created"], "status": pi["status"]}
print("✓ المزوّد جاهز")
