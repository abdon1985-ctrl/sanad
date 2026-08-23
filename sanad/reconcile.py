# -*- coding: utf-8 -*-
"""Reconciliation — settlement comes from reality, not assumption.

Proven in EXP-001 and EXP-003b:
- On startup, every PENDING/UNRESOLVED execution is re-settled.
- Strategy: saved receipt first (direct retrieve, instant), provider search
  by execution_id as fallback (covers death-before-receipt-saved).
- Outcomes: EXECUTED (found), NOT_EXECUTED (no trace — safe to retry with a
  NEW approval), UNRESOLVED (couldn't settle this time; re-enters next run).
"""
from .claims import ClaimStore
from .ledger import Ledger
from .providers import Provider


def recover_on_startup(ledger: Ledger, claims: ClaimStore, provider: Provider):
    settled = []
    for approval_id, execution_id in claims.unresolved():
        status, detail, already_counted = _settle(ledger, provider,
                                                  execution_id)
        claims.set_status(execution_id, status)
        # EXP-012 (F3): a settlement that establishes the money DID move
        # is spend, and the budget must see it. The amount comes from the
        # GRANTED approval row — the authoritative record of what was
        # authorized. It is written ONLY when no execute/EXECUTED row
        # already counted this execution, so the two can never sum twice.
        amount = 0
        if status == "EXECUTED" and not already_counted:
            granted = ledger.last(stage="approval", state="GRANTED",
                                  approval_id=approval_id)
            amount = granted.get("amount_minor", 0) if granted else 0
        ledger.append("resolve", status, detail,
                      approval_id=approval_id, execution_id=execution_id,
                      amount_minor=amount)
        settled.append((execution_id, status))
    return settled


def _settle(ledger: Ledger, provider: Provider, execution_id: str):
    receipt_row = ledger.last(stage="execute", state="EXECUTED",
                              execution_id=execution_id)
    try:
        if receipt_row and receipt_row.get("receipt"):
            found = provider.retrieve(receipt_row["receipt"])
            return ("EXECUTED",
                    f"receipt={found['receipt']} (direct retrieve)", True)
        found = provider.find_by_execution_id(execution_id)
        if found:
            return ("EXECUTED",
                    f"receipt={found['receipt']} (found by search)", False)
        return "NOT_EXECUTED", "no trace at provider — safe to retry", False
    except Exception as e:
        return "UNRESOLVED", f"settlement failed: {type(e).__name__}", False
