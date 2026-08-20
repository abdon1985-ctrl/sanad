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
        status, detail = _settle(ledger, provider, execution_id)
        claims.set_status(execution_id, status)
        ledger.append("resolve", status, detail,
                      approval_id=approval_id, execution_id=execution_id)
        settled.append((execution_id, status))
    return settled


def _settle(ledger: Ledger, provider: Provider, execution_id: str):
    receipt_row = ledger.last(stage="execute", state="EXECUTED",
                              execution_id=execution_id)
    try:
        if receipt_row and receipt_row.get("receipt"):
            found = provider.retrieve(receipt_row["receipt"])
            return "EXECUTED", f"receipt={found['receipt']} (direct retrieve)"
        found = provider.find_by_execution_id(execution_id)
        if found:
            return "EXECUTED", f"receipt={found['receipt']} (found by search)"
        return "NOT_EXECUTED", "no trace at provider — safe to retry"
    except Exception as e:
        return "UNRESOLVED", f"settlement failed: {type(e).__name__}"
