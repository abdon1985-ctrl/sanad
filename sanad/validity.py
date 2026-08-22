# -*- coding: utf-8 -*-
"""Sanad validity (EXP-006) — authorization has a lifetime and a standing.

EXP-002 froze the TERMS: the snapshot at signing time governs, not
today's file. This module adds the two questions the snapshot cannot
answer, because they are alive:

1. TIME   — has this signed document expired? (`valid_days`, enforced,
            never a dead clause: absent means "no expiry", present means
            it is checked on every verification.)
2. STANDING — is the signer's key still trusted? Revocation is
            IMMEDIATE and FORWARD-ONLY: a revoked key authorizes no new
            act, even under a document signed before revocation. What
            was already executed stays in the ledger untouched — history
            is never rewritten.

Design rules:
- The clock is injected (`clock=time.time`). Tests move time by moving
  the clock, never by sleeping: a time property you can only test by
  waiting is a property you will stop testing.
- Revocation is a ledger act with a reason. Trust changes are events,
  not silent dictionary edits.
- `verify_untampered(sig)` adapts the signed path to the Gateway's
  existing contract, so the crypto path becomes the LIVE path with no
  Gateway change.
"""
import time

from .identity import Signer, SignedPreAuthorization, verify


class TrustedKeys:
    """The registry that EXP-005 left as a bare dict — now an object
    whose changes are ledger events.

    Granting a key stays an out-of-band admin act (handing a badge).
    Revoking one is recorded: who lost trust, why, and when."""

    def __init__(self, ledger, initial: dict = None, clock=time.time):
        self.ledger = ledger
        self.clock = clock
        self._keys = dict(initial or {})
        self._revoked = {}          # name -> revocation ledger row

    def grant(self, name: str, public_key_b64: str):
        self._keys[name] = public_key_b64
        self._revoked.pop(name, None)

    def revoke(self, name: str, reason: str):
        row = self.ledger.append(
            "key", "REVOKED",
            f"trusted key for '{name}' revoked — {reason}",
            approver=name, reason=reason, revoked_at=self.clock())
        self._revoked[name] = row
        return row

    def is_revoked(self, name: str) -> bool:
        return name in self._revoked

    def get(self, name: str):
        """dict-compatible: returns the key ONLY if still in standing."""
        if name in self._revoked:
            return None
        return self._keys.get(name)

    def raw_key(self, name: str):
        """The key regardless of standing — for reading history only."""
        return self._keys.get(name)


class TimedSignedPreAuthorization(SignedPreAuthorization):
    """EXP-005's signed document + EXP-006's lifetime and standing.

    `trusted_keys` may be a plain dict (EXP-005 behaviour, no
    revocation) or a TrustedKeys registry (standing enforced)."""

    def __init__(self, ledger, document_path: str, trusted_keys,
                 clock=time.time):
        super().__init__(ledger, document_path, trusted_keys)
        self.clock = clock

    # signing goes through timed_sign() below — one explicit path that
    # stamps signed_at from the same injected clock the verifier uses.

    # ---------- verification: EXP-005 checks + time + standing ----------
    def verify_authorization(self, sig_row) -> tuple:
        name = sig_row.get("approver")

        # 1. standing — checked FIRST: a revoked key fails even before
        #    any byte is read. Immediate and forward-only.
        if isinstance(self.trusted_keys, TrustedKeys):
            if self.trusted_keys.is_revoked(name):
                return False, (f"key for '{name}' was revoked — no new "
                               "act may derive from this signature")

        # 2..4. the EXP-005 checks (trusted key, key match, signature
        #        over current bytes)
        ok, reason = super().verify_authorization(sig_row)
        if not ok:
            return ok, reason

        # 5. time — valid_days, enforced from the SIGNED terms snapshot
        terms = sig_row.get("terms", {})
        valid_days = terms.get("valid_days")
        if valid_days is not None:
            signed_at = sig_row.get("signed_at")
            if signed_at is None:
                return False, ("document declares valid_days but the "
                               "signature has no signed_at — cannot "
                               "prove it is still alive")
            age = self.clock() - signed_at
            if age > valid_days * 86400:
                return False, (f"pre-authorization expired — signed "
                               f"{age/86400:.1f} days ago, valid for "
                               f"{valid_days}")
        return True, "verified"

    # ---------- the wiring adapter: Gateway compatibility ----------
    def verify_untampered(self, sig_row):
        """Same contract as the name-based PreAuthorization, so the
        existing Gateway runs on the SIGNED path with no change.
        Returns (ok, detail)."""
        ok, reason = self.verify_authorization(sig_row)
        return ok, reason


def timed_sign(pre: TimedSignedPreAuthorization, signer: Signer):
    """Sign and stamp signed_at into the ledger row itself.

    Kept as an explicit function (not hidden inside .sign) so the
    signing instant visibly comes from the same injected clock the
    verifier will use."""
    import hashlib, json, uuid
    with open(pre.document_path, "rb") as f:
        raw = f.read()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    signature = signer.sign(raw)
    pa_id = "PRE-AUTH-" + uuid.uuid4().hex[:8]
    return pre.ledger.append(
        "pre_auth", "SIGNED",
        f"{pa_id} by {signer.name} — hash {digest}, Ed25519 signed",
        pre_auth_id=pa_id, pre_auth_hash=digest,
        approver=signer.name,
        public_key=signer.public_key_b64,
        signature=signature,
        signed_at=pre.clock(),
        terms=json.loads(raw.decode("utf-8")))
