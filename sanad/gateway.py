# -*- coding: utf-8 -*-
"""The Action Gateway — one execution path for everyone.

The only difference between autonomous and human-approved execution is the
`approver` field. Flow (proven across EXP-000..004):

    request -> derive approval from signed pre-auth?
      yes, within limits -> approval GRANTED (derived_from=...) -> atomic claim -> execute
      no                 -> ESCALATE_HUMAN / DENIED (with reason), zero provider calls

Invariants enforced here:
- No execution without a matching approval (amount + currency exactly).
- Approval is consumed by an atomic claim BEFORE the provider call.
- Tampered pre-auth document => the whole document falls (EXP-004).
- Silence is cancellation, never consent.
"""
import uuid

from .claims import ClaimStore
from .ledger import Ledger
from .policy import PreAuthorization
from .providers import Provider, ProviderRejected


class Gateway:
    def __init__(self, ledger: Ledger, claims: ClaimStore,
                 pre_auth: PreAuthorization, provider: Provider):
        self.ledger = ledger
        self.claims = claims
        self.pre_auth = pre_auth
        self.provider = provider

    # ---------- approval derivation (EXP-004) ----------
    def derive_approval(self, item: str, amount_minor: int, currency: str):
        sig = self.pre_auth.current_signature()
        if sig is None:
            self.ledger.append("approval", "DENIED", "no signed pre-authorization")
            return None

        ok, current_hash = self.pre_auth.verify_untampered(sig)
        if not ok:
            self.ledger.append(
                "approval", "DENIED_PRE_AUTH_HASH_MISMATCH",
                f"document changed after signing: signed {sig['pre_auth_hash']}, "
                f"now {current_hash} — a new signature is required",
                signed_hash=sig["pre_auth_hash"], current_hash=current_hash)
            return None

        terms = sig["terms"]  # snapshot from the ledger, never the live file
        if currency.upper() != terms.get("currency", "USD").upper():
            self.ledger.append("approval", "DENIED",
                               f"currency {currency} outside pre-auth "
                               f"({terms.get('currency','USD')})")
            return None
        if item in terms.get("blocked_categories", []):
            self.ledger.append("approval", "ESCALATE_HUMAN",
                               f"category '{item}' requires human approval")
            return None
        if amount_minor > terms["auto_limit_minor"]:
            self.ledger.append("approval", "ESCALATE_HUMAN",
                               f"{amount_minor} above auto limit "
                               f"{terms['auto_limit_minor']}")
            return None
        spent = self.ledger.spent_today_minor()
        if spent + amount_minor > terms["daily_budget_minor"]:
            self.ledger.append("approval", "DENIED_DAILY_BUDGET",
                               f"spent {spent} + {amount_minor} exceeds "
                               f"{terms['daily_budget_minor']}")
            return None

        approval_id = "AP-" + uuid.uuid4().hex[:8]
        self.ledger.append(
            "approval", "GRANTED",
            f"{approval_id} derived from {sig['pre_auth_id']} — "
            f"{item} for {amount_minor} {currency}",
            approval_id=approval_id, derived_from=sig["pre_auth_id"],
            pre_auth_hash=sig["pre_auth_hash"],
            approver=sig["approver"] + " (pre-auth)",
            amount_minor=amount_minor, currency=currency, item=item)
        return {"approval_id": approval_id, "amount_minor": amount_minor,
                "currency": currency, "item": item}

    def grant_human_approval(self, item, amount_minor, currency, approver: str):
        """The human path — same shape, human approver, zero derivation."""
        approval_id = "AP-" + uuid.uuid4().hex[:8]
        self.ledger.append(
            "approval", "GRANTED",
            f"{approval_id} by human {approver} — {item} for "
            f"{amount_minor} {currency}",
            approval_id=approval_id, approver=approver,
            amount_minor=amount_minor, currency=currency, item=item)
        return {"approval_id": approval_id, "amount_minor": amount_minor,
                "currency": currency, "item": item}

    # ---------- execution (EXP-003 core) ----------
    def execute(self, approval: dict):
        if approval is None:
            return None
        execution_id = "EX-" + uuid.uuid4().hex[:8]

        # (1) atomic claim in SQLite — the race arbiter
        if not self.claims.try_claim(approval["approval_id"], execution_id):
            return self.ledger.append(
                "execute", "DENIED_APPROVAL_CONSUMED",
                f"{approval['approval_id']} already claimed",
                approval_id=approval["approval_id"])

        # (2) proof line in the ledger — after atomicity, before the world
        self.ledger.append("claim", "CLAIMED",
                           f"{approval['approval_id']} -> {execution_id}",
                           approval_id=approval["approval_id"],
                           execution_id=execution_id)
        self.ledger.append("pre-action", "SENT",
                           f"{approval['amount_minor']} "
                           f"{approval['currency']} — before the call",
                           execution_id=execution_id,
                           amount_minor=approval["amount_minor"],
                           currency=approval["currency"])

        # (3) touch the world
        try:
            result = self.provider.execute(approval["amount_minor"],
                                           approval["currency"], execution_id)
            self.claims.set_status(execution_id, "EXECUTED")
            return self.ledger.append(
                "execute", "EXECUTED",
                f"receipt={result['receipt']}",
                execution_id=execution_id, receipt=result["receipt"],
                amount_minor=result["amount_minor"])
        except ProviderRejected as e:
            self.claims.set_status(execution_id, "REJECTED")
            return self.ledger.append("execute", "REJECTED", str(e),
                                      execution_id=execution_id)
        except Exception as e:
            # UNKNOWN: response never arrived. Stays PENDING for reconcile —
            # the approval stays burned; settlement comes from reality.
            return self.ledger.append("execute", "UNKNOWN",
                                      f"({type(e).__name__}) response lost",
                                      execution_id=execution_id)
