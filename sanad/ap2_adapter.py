# -*- coding: utf-8 -*-
"""Sanad AP2 adapter (EXP-011) — accountability on top of someone
else's authorization.

AP2 (Agent Payments Protocol, Google -> FIDO Alliance) produces a
cryptographically signed answer to ONE question: did a human authorize
this specific purchase? Its PaymentMandate carries the user's
authorization; its CartMandate carries the merchant's signed cart.

AP2 deliberately does not answer three questions Sanad exists for:
  - WHICH agent used the authorization (AP2 has no agent identity);
  - whether the OPERATOR's own policy allowed the act (a daily budget
    across many mandates is not AP2's concern);
  - what happened when the provider went SILENT.

This adapter takes real AP2 mandate structures (the shapes in
google-agentic-commerce/AP2, models/mandate.py) and runs them through
Sanad's full path: anchor -> agent-bound approval -> policy -> atomic
claim -> UNKNOWN -> reconciliation.

DECLARED LIMIT, stated before anyone asks: Sanad does NOT verify the
JWT / verifiable-credential signatures inside `user_authorization` and
`merchant_authorization`. That verification belongs to the credential
provider and the payment network — re-implementing it here would be
theater. What Sanad does instead is ANCHOR: it writes the hashes of
the exact mandate bytes it relied on into the chained ledger, so any
later dispute can establish precisely what was presented, to whom it
was bound, and what came of it. Sanad's positioning in one line:

    Sanad doesn't authorize agents to pay. It makes agent
    authorization accountable after the authorization exists.
"""
import hashlib
import json
import time
from datetime import datetime, timezone

from .binding import BoundGateway

ANCHOR_STAGE = "ap2_anchor"


def _canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _minor(amount: dict) -> int:
    """W3C PaymentCurrencyAmount value is a float of major units."""
    return int(round(float(amount["value"]) * 100))


def _parse_iso(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class Ap2Anchor:
    """Anchors one AP2 (CartMandate, PaymentMandate) pair into the ledger
    and exposes it to the Gateway as a pre-authorization.

    `daily_budget_minor` is deliberately a SANAD parameter, not an AP2
    field: AP2 authorizes one purchase; the budget across purchases is
    the operator's policy, layered on top. That layering is the point.
    """

    def __init__(self, ledger, cart: dict, payment: dict,
                 daily_budget_minor: int = 50000, clock=time.time):
        self.ledger = ledger
        self.cart = cart
        self.payment = payment
        self.daily_budget_minor = daily_budget_minor
        self.clock = clock
        self.anchor = None
        self._anchor()

    # ---------- anchoring: the checks Sanad CAN make, honestly --------
    def _deny(self, state, detail, **fields):
        self.ledger.append(ANCHOR_STAGE, state, detail, **fields)
        return None

    def _anchor(self):
        contents = self.cart.get("contents", {})
        pmc = self.payment.get("payment_mandate_contents", {})

        ua = self.payment.get("user_authorization")
        if not ua:
            return self._deny(
                "DENIED_AP2_UNAUTHORIZED",
                "PaymentMandate carries no user_authorization — Sanad "
                "refuses to act on an unauthorized mandate")

        cart_total = contents.get("payment_request", {}) \
                             .get("details", {}).get("total", {})
        pm_total = pmc.get("payment_details_total", {})
        if (_minor(cart_total["amount"]) != _minor(pm_total["amount"])
                or cart_total["amount"]["currency"].upper()
                != pm_total["amount"]["currency"].upper()):
            return self._deny(
                "DENIED_AP2_TOTAL_MISMATCH",
                f"cart says {cart_total['amount']} but payment mandate "
                f"says {pm_total['amount']} — the two halves disagree")

        if self.clock() > _parse_iso(contents["cart_expiry"]):
            return self._deny(
                "DENIED_AP2_EXPIRED",
                f"cart expired at {contents['cart_expiry']}")

        self.anchor = self.ledger.append(
            ANCHOR_STAGE, "ANCHORED",
            f"AP2 mandate {pmc.get('payment_mandate_id')} anchored — "
            "signature verification is the credential provider's job; "
            "these hashes fix what was presented",
            payment_mandate_id=pmc.get("payment_mandate_id"),
            merchant=contents.get("merchant_name"),
            amount_minor=_minor(pm_total["amount"]),
            currency=pm_total["amount"]["currency"].upper(),
            cart_expiry=contents["cart_expiry"],
            cart_hash=_canonical_hash(contents),
            payment_mandate_hash=_canonical_hash(pmc),
            user_authorization_hash=hashlib.sha256(
                ua.encode("utf-8")).hexdigest(),
            merchant_authorization_hash=hashlib.sha256(
                (self.cart.get("merchant_authorization") or "")
                .encode("utf-8")).hexdigest())
        return self.anchor

    # ---------- the pre_auth interface the Gateway already speaks -----
    def current_signature(self):
        if self.anchor is None:
            return None
        return {
            "pre_auth_id": "AP2-" + self.anchor["payment_mandate_id"],
            "pre_auth_hash": self.anchor["payment_mandate_hash"],
            "approver": "ap2:user_authorization",
            "terms": {
                "currency": self.anchor["currency"],
                "blocked_categories": [],
                "auto_limit_minor": self.anchor["amount_minor"],
                "daily_budget_minor": self.daily_budget_minor,
            },
        }

    def verify_untampered(self, sig_row):
        """The in-memory mandate must still be the anchored one."""
        pmc = self.payment.get("payment_mandate_contents", {})
        current = _canonical_hash(pmc)
        return (current == self.anchor["payment_mandate_hash"], current)


class Ap2BoundGateway(BoundGateway):
    """BoundGateway with AP2's own semantics enforced up front:

    an AP2 mandate authorizes ONE cart at ONE price — it is not a
    ceiling. An amount merely *under* the total is as unauthorized as
    one above it.
    """

    def derive_approval(self, item, amount_minor, currency):
        a = self.pre_auth.anchor
        if a is None:
            self.ledger.append("approval", "DENIED",
                               "no anchored AP2 mandate")
            return None
        if self.clock() > _parse_iso(a["cart_expiry"]):
            self.ledger.append(
                "approval", "DENIED_AP2_EXPIRED",
                f"cart expired at {a['cart_expiry']} — no approval, "
                "no claim, no provider call")
            return None
        if (amount_minor != a["amount_minor"]
                or currency.upper() != a["currency"]):
            self.ledger.append(
                "approval", "DENIED_AP2_AMOUNT_MISMATCH",
                f"mandate authorizes exactly {a['amount_minor']} "
                f"{a['currency']}, requested {amount_minor} {currency} — "
                "an AP2 mandate is a cart, not a ceiling")
            return None
        return super().derive_approval(item, amount_minor, currency)
