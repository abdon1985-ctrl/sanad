# -*- coding: utf-8 -*-
"""Sanad timed gateway (EXP-006b) — the signed path becomes the LIVE path,
and an approval gets a lifetime.

Two gaps this closes:

1. WIRING — Gateway.derive_approval only knew the name-based
   PreAuthorization. EXP-005/006 built the cryptographic path
   (TimedSignedPreAuthorization) beside it, not inside it. Here the
   Gateway checks the FULL authorization (trusted key, no key swap,
   signature over current bytes, document expiry, signer standing)
   BEFORE the term checks — with honest denial states, not a
   hash-mismatch message for a revoked key.

2. APPROVAL TTL — until now an approval, once granted, lived forever
   until consumed. Now `approval_ttl_seconds` in the signed terms
   gives it a lifetime: expired approvals are refused BEFORE the
   claim, so they are never consumed and never reach a provider.
   Absent means no expiry — same rule as valid_days: a term is
   enforced or it does not exist.

The clock is injected, same as EXP-006: tests move time, never wait.
"""
import time

from .gateway import Gateway
from .validity import TimedSignedPreAuthorization


class TimedGateway(Gateway):
    def __init__(self, ledger, claims, pre_auth, provider, clock=time.time):
        super().__init__(ledger, claims, pre_auth, provider)
        self.clock = clock

    # ---------- derivation: full authorization first, honest states ----
    def derive_approval(self, item: str, amount_minor: int, currency: str):
        if isinstance(self.pre_auth, TimedSignedPreAuthorization):
            sig = self.pre_auth.current_signature()
            if sig is None:
                self.ledger.append("approval", "DENIED",
                                   "no signed pre-authorization")
                return None
            ok, reason = self.pre_auth.verify_authorization(sig)
            if not ok:
                self.ledger.append(
                    "approval", "DENIED_AUTHORIZATION_INVALID", reason,
                    signed_hash=sig.get("pre_auth_hash"))
                return None

        approval = super().derive_approval(item, amount_minor, currency)
        if approval is not None:
            approval["issued_at"] = self.clock()
            sig = self.pre_auth.current_signature()
            terms = (sig or {}).get("terms", {})
            approval["ttl_seconds"] = terms.get("approval_ttl_seconds")
        return approval

    def grant_human_approval(self, item, amount_minor, currency,
                             approver: str, ttl_seconds=None):
        approval = super().grant_human_approval(item, amount_minor,
                                                currency, approver)
        approval["issued_at"] = self.clock()
        approval["ttl_seconds"] = ttl_seconds
        return approval

    # ---------- execution: expiry refused BEFORE the claim ------------
    def execute(self, approval: dict):
        if approval is None:
            return None
        ttl = approval.get("ttl_seconds")
        if ttl is not None:
            age = self.clock() - approval.get("issued_at", 0)
            if age > ttl:
                return self.ledger.append(
                    "execute", "DENIED_APPROVAL_EXPIRED",
                    f"{approval['approval_id']} issued {age:.0f}s ago, "
                    f"ttl {ttl}s — approval not consumed, no provider call",
                    approval_id=approval["approval_id"])
        return super().execute(approval)
