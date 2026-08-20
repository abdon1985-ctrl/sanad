# -*- coding: utf-8 -*-
"""Stripe provider — the implementation validated in EXP-001/003/004.

Reconcile strategy (EXP-003b): try the saved receipt first via direct
retrieve (instant, no index), fall back to metadata search (which can lag
up to ~1 minute behind creation). The saved receipt in your ledger spares
you the search; the search exists for the case where the system died
before the receipt was saved.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import Provider, ProviderRejected

API = "https://api.stripe.com"


class StripeProvider(Provider):
    name = "stripe"

    def __init__(self, secret_key: str, timeout: int = 15):
        if not secret_key or not secret_key.startswith("sk_"):
            raise ValueError("A Stripe secret key (sk_...) is required.")
        self.key = secret_key
        self.timeout = timeout

    def _request(self, path, body=None, idem=None):
        data = urllib.parse.urlencode(body).encode() if body else None
        req = urllib.request.Request(API + path, data=data)
        req.add_header("Authorization", "Bearer " + self.key)
        if idem:
            req.add_header("Idempotency-Key", idem)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def execute(self, amount_minor, currency, execution_id):
        try:
            pi = self._request("/v1/payment_intents", {
                "amount": amount_minor,
                "currency": currency.lower(),
                "payment_method_types[]": "card",
                "metadata[execution_id]": execution_id,
            }, idem="sanad-" + execution_id)
            return {"receipt": pi["id"], "amount_minor": pi["amount"]}
        except urllib.error.HTTPError as e:
            raise ProviderRejected(f"HTTP {e.code}") from e

    def retrieve(self, receipt):
        pi = self._request("/v1/payment_intents/" + receipt)
        return {"receipt": pi["id"], "amount_minor": pi["amount"]}

    def find_by_execution_id(self, execution_id):
        q = urllib.parse.quote(f"metadata['execution_id']:'{execution_id}'")
        data = self._request(f"/v1/payment_intents/search?query={q}")
        found = data.get("data", [])
        if found:
            return {"receipt": found[0]["id"], "amount_minor": found[0]["amount"]}
        return None
