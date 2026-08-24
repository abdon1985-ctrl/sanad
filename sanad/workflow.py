# -*- coding: utf-8 -*-
"""Sanad workflow (EXP-013) — three agents, three roles, one authority.

Every experiment so far had agents that were interchangeable: three
peers under one mandate, each able to do the same things. This one
gives them DIFFERENT POWERS and asks whether Sanad can hold a decision
that needs more than one of them.

The roles, and the line between them:

    Procurement  proposes.  It may search, compare, and ask. It may
                 NOT derive an approval, and it has no gateway at all —
                 it physically cannot reach a provider.
    Finance      endorses.  It reads a proposal and decides whether to
                 carry it to Sanad. Its endorsement is an opinion, not
                 a permission: Sanad re-checks everything.
    Audit        reads.     It sees the whole ledger and reports. It
                 has no veto — a reader that can block is a second
                 authority, and there is only one authority here.

The invariant this module exists to test:

    NO AGENT EXECUTES. An agent proposes or endorses; Sanad decides.

Nothing in `gateway.py`, `claims.py` or `ledger.py` changes. A
proposal is a ledger row, an endorsement is a ledger row, and a
`workflow_id` threads proposal -> endorsement -> approval -> execution
into one auditable chain. Sanad's atomicity, binding and settlement
are inherited untouched — which is the point: adding a workflow must
not require weakening the gateway.

DECLARED GAP, frozen up front: an endorsement carries no signature.
Finance's endorsement is a ledger claim of a decision, not proof of
identity — anyone able to append to this ledger can write an
endorsement row. Sanad still refuses to act on it beyond the frozen
mandate (test W3), so a forged endorsement buys nothing; but the row
itself is testimony, not evidence. Signing endorsements is the same
open question as agent key custody.
"""
import time
import uuid

PROPOSAL_STAGE = "proposal"
ENDORSEMENT_STAGE = "endorsement"


def new_workflow_id() -> str:
    return "WF-" + uuid.uuid4().hex[:8]


class ProcurementAgent:
    """Proposes. Holds no gateway — it cannot reach a provider at all."""

    def __init__(self, ledger, name="procurement", clock=time.time):
        self.ledger = ledger
        self.name = name
        self.clock = clock

    def propose(self, item, amount_minor, currency, justification="",
                workflow_id=None):
        workflow_id = workflow_id or new_workflow_id()
        self.ledger.append(
            PROPOSAL_STAGE, "PROPOSED",
            f"{self.name} proposes {item} for {amount_minor} {currency}"
            + (f" — {justification}" if justification else ""),
            workflow_id=workflow_id, agent=self.name, item=item,
            amount_minor=amount_minor, currency=currency,
            justification=justification)
        return {"workflow_id": workflow_id, "item": item,
                "amount_minor": amount_minor, "currency": currency,
                "justification": justification, "proposed_by": self.name}


class FinanceAgent:
    """Endorses, and carries the endorsement to Sanad.

    It holds a gateway because someone must ask Sanad — but holding a
    gateway is not holding authority: every endorsement is re-checked
    against the frozen mandate, and Finance cannot change that mandate.
    """

    def __init__(self, ledger, gateway, name="finance"):
        self.ledger = ledger
        self.gateway = gateway
        self.name = name

    def endorse(self, proposal, note=""):
        """An opinion, recorded. Sanad is not bound by it."""
        self.ledger.append(
            ENDORSEMENT_STAGE, "ENDORSED",
            f"{self.name} endorses {proposal['item']} "
            f"({proposal['amount_minor']} {proposal['currency']})"
            + (f" — {note}" if note else ""),
            workflow_id=proposal["workflow_id"], agent=self.name,
            amount_minor=proposal["amount_minor"],
            currency=proposal["currency"], note=note)
        return dict(proposal, endorsed_by=self.name, note=note)

    def carry_to_sanad(self, endorsed):
        """Sanad decides. Every check runs again, from the document."""
        approval = self.gateway.derive_approval(
            endorsed["item"], endorsed["amount_minor"],
            endorsed["currency"])
        if approval is None:
            last = [r for r in self.ledger.rows()
                    if r["stage"] == "approval"][-1]
            self.ledger.append(
                ENDORSEMENT_STAGE, "REFUSED_BY_SANAD",
                f"endorsement did not survive the mandate: {last['state']}",
                workflow_id=endorsed["workflow_id"], agent=self.name,
                sanad_state=last["state"])
            return None
        self.ledger.append(
            ENDORSEMENT_STAGE, "APPROVED_BY_SANAD",
            f"approval {approval['approval_id']} derived",
            workflow_id=endorsed["workflow_id"], agent=self.name,
            approval_id=approval["approval_id"])
        row = self.gateway.execute(approval)
        self.ledger.append(
            ENDORSEMENT_STAGE, "SETTLED",
            f"execution ended {row['state']}",
            workflow_id=endorsed["workflow_id"], agent=self.name,
            execution_id=row.get("execution_id"), result=row["state"])
        return row


class AuditAgent:
    """Reads the ledger. Reports. Never blocks.

    Audit exists to make collusion *visible after the fact*, which is a
    different and more honest promise than preventing it.
    """

    def __init__(self, ledger, name="audit"):
        self.ledger = ledger
        self.name = name

    def trace(self, workflow_id):
        return [r for r in self.ledger.rows()
                if r.get("workflow_id") == workflow_id]

    def report(self, workflow_id):
        rows = self.trace(workflow_id)
        proposed = next((r for r in rows if r["stage"] == PROPOSAL_STAGE),
                        None)
        endorsed = next((r for r in rows
                         if r["stage"] == ENDORSEMENT_STAGE
                         and r["state"] == "ENDORSED"), None)
        refused = next((r for r in rows
                        if r["state"] == "REFUSED_BY_SANAD"), None)
        settled = next((r for r in rows if r["state"] == "SETTLED"), None)

        findings = []
        # An endorsement for more than was proposed: Finance did not
        # endorse the proposal, it endorsed something else.
        if proposed and endorsed and \
                endorsed["amount_minor"] != proposed["amount_minor"]:
            findings.append(
                f"ENDORSEMENT_EXCEEDS_PROPOSAL: proposed "
                f"{proposed['amount_minor']}, endorsed "
                f"{endorsed['amount_minor']}")
        # Both agents moved and Sanad still said no: the two of them
        # agreed on something the mandate does not allow.
        if endorsed and refused:
            findings.append(
                f"COLLUSION_REFUSED_BY_MANDATE: {refused['sanad_state']} "
                f"— both agents moved, the mandate did not")
        # Language aimed at changing policy rather than describing a
        # purchase. Named plainly as a heuristic, not a detector.
        if proposed:
            text = (proposed.get("justification") or "").lower()
            for phrase in ("raise the limit", "increase the limit",
                           "override", "just this once", "bypass"):
                if phrase in text:
                    findings.append(
                        f"POLICY_PRESSURE_LANGUAGE: '{phrase}' in the "
                        "justification — a proposal argued about the "
                        "policy, not the purchase")
                    break

        return {"workflow_id": workflow_id, "rows": len(rows),
                "proposed_minor": proposed["amount_minor"] if proposed else 0,
                "endorsed_minor": endorsed["amount_minor"] if endorsed else 0,
                "outcome": settled["result"] if settled
                           else (refused["sanad_state"] if refused
                                 else "INCOMPLETE"),
                "findings": findings}
