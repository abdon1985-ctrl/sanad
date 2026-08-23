# -*- coding: utf-8 -*-
"""Sanad binding (EXP-009) — the actor becomes part of the document.

EXP-008 put three agents under one mandate and found two gaps that a
single agent could never expose:

1. AN APPROVAL WAS A BEARER TOKEN. It carried an amount and a currency
   and no holder, so any agent could execute any other agent's
   approval — and the ledger could not even name who acted.
2. THE MANDATE WAS "THE LAST SIGNED ROW". Anyone able to append to the
   ledger could displace it: the forged signature was correctly
   refused, and it froze the honest agents along with the attacker.

Both are the same absence: the actor was never part of the document.
This module adds it, without touching Gateway or TimedGateway.

    PinnedPreAuthorization — the mandate is ONE named pre_auth_id,
        chosen explicitly. Later signed rows are visible in the ledger
        (nothing is ever hidden) but they do not become the mandate.
        Moving to a new mandate is a deliberate act, not a side effect
        of someone else writing a line.

    BoundGateway — each agent's gateway holds that agent's Signer.
        Deriving an approval records a BOUND row naming the agent and
        its public key. Executing requires a fresh signature over the
        approval_id, verified against the key recorded at derivation.
        A stolen approval dict is useless: the binding lives in the
        append-only ledger, not in the object, and the proof needs a
        private key the thief does not have.

Refusal happens BEFORE the atomic claim, so a rejected theft never
consumes the victim's approval — a defence that burned the thing it
protects would just be a slower attack.

Declared gap, honestly: this binds an approval to an agent KEY. Where
that key lives, and how an agent is issued one, is the same unanswered
question as EXP-005's private key — now asked about agents too.
"""
import time

from .gateway_timed import TimedGateway
from .identity import Signer, verify
from .validity import TimedSignedPreAuthorization


class PinnedPreAuthorization(TimedSignedPreAuthorization):
    """A mandate that is a specific signed document, not the latest one."""

    def __init__(self, ledger, document_path, trusted_keys, pre_auth_id,
                 clock=time.time):
        super().__init__(ledger, document_path, trusted_keys, clock=clock)
        self.pre_auth_id = pre_auth_id

    def current_signature(self):
        return self.ledger.last(stage="pre_auth", state="SIGNED",
                                pre_auth_id=self.pre_auth_id)


def pin(pre_or_row):
    """Convenience: take the row returned by timed_sign and read its id."""
    return pre_or_row["pre_auth_id"]


class BoundGateway(TimedGateway):
    """A gateway that acts AS an agent, and can prove it."""

    def __init__(self, ledger, claims, pre_auth, provider, agent: Signer,
                 clock=time.time):
        super().__init__(ledger, claims, pre_auth, provider, clock=clock)
        self.agent = agent

    # ---------- derivation: record who this approval belongs to -------
    def derive_approval(self, item, amount_minor, currency):
        approval = super().derive_approval(item, amount_minor, currency)
        if approval is None:
            return None
        self.ledger.append(
            "binding", "BOUND",
            f"{approval['approval_id']} bound to agent '{self.agent.name}'",
            approval_id=approval["approval_id"],
            agent=self.agent.name,
            agent_key=self.agent.public_key_b64)
        approval["agent"] = self.agent.name
        return approval

    # ---------- execution: prove it, before the claim -----------------
    def execute(self, approval):
        if approval is None:
            return None
        approval_id = approval.get("approval_id")
        bound = self.ledger.last(stage="binding", state="BOUND",
                                 approval_id=approval_id)
        if bound is None:
            return self.ledger.append(
                "execute", "DENIED_APPROVAL_UNBOUND",
                f"{approval_id} has no binding row — it belongs to no agent",
                approval_id=approval_id, agent=self.agent.name)

        proof = self.agent.sign(approval_id.encode("utf-8"))
        if not verify(bound["agent_key"], approval_id.encode("utf-8"), proof):
            return self.ledger.append(
                "execute", "DENIED_APPROVAL_NOT_YOURS",
                f"{approval_id} is bound to '{bound['agent']}' — "
                f"'{self.agent.name}' cannot prove that key; "
                "approval not consumed, no provider call",
                approval_id=approval_id, agent=self.agent.name,
                bound_to=bound["agent"])

        return super().execute(approval)
