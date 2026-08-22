# -*- coding: utf-8 -*-
"""Sanad identity — a signature is mathematics, not a string.

Before this module, `approver="mohamed"` was a claim anyone could type.
After it, a signature is an Ed25519 proof over the exact bytes of the
authorization document, verifiable by anyone holding the public key and
forgeable by no one without the private key.

Design rules:
- The private key NEVER touches the ledger. Only the public key and the
  signature are recorded — the ledger stays safe to publish.
- The signature covers raw document bytes (same byte-exactness rule as
  the hash in EXP-004): re-serialization or "equivalent" JSON does not
  verify. A signature covers a document, not its meaning.
- Verification failure is DENIED_INVALID_SIGNATURE — a distinct state,
  because "no signature" and "forged signature" are different facts.
"""
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.exceptions import InvalidSignature


class Signer:
    """A human (or later: an agent) identity holding a private key."""

    def __init__(self, name: str, private_key: Ed25519PrivateKey = None):
        self.name = name
        self._private = private_key or Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat)
        raw = self._private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self._private.sign(message)).decode()


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, KeyError):
        return False


class SignedPreAuthorization:
    """EXP-005 flow: signing snapshots bytes + cryptographic signature."""

    def __init__(self, ledger, document_path: str, trusted_keys: dict):
        """trusted_keys: {name: public_key_b64} — who Sanad accepts.
        Registering a key is an out-of-band act (an admin decision),
        exactly like handing someone a badge."""
        self.ledger = ledger
        self.document_path = document_path
        self.trusted_keys = trusted_keys

    def sign(self, signer: Signer):
        import hashlib, json, uuid
        with open(self.document_path, "rb") as f:
            raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()[:12]
        signature = signer.sign(raw)
        pa_id = "PRE-AUTH-" + uuid.uuid4().hex[:8]
        return self.ledger.append(
            "pre_auth", "SIGNED",
            f"{pa_id} by {signer.name} — hash {digest}, Ed25519 signed",
            pre_auth_id=pa_id, pre_auth_hash=digest,
            approver=signer.name,
            public_key=signer.public_key_b64,
            signature=signature, terms=json.loads(raw.decode("utf-8")))

    def current_signature(self):
        return self.ledger.last(stage="pre_auth", state="SIGNED")

    def verify_authorization(self, sig_row) -> tuple:
        """The three checks that turn a name into an identity:
        1. the approver's key is one Sanad trusts
        2. the recorded public key matches the trusted one (no key swap)
        3. the signature verifies over the CURRENT file bytes
        Returns (ok, reason)."""
        name = sig_row.get("approver")
        trusted = self.trusted_keys.get(name)
        if trusted is None:
            return False, f"approver '{name}' has no trusted key"
        if sig_row.get("public_key") != trusted:
            return False, f"public key mismatch for '{name}' — possible key swap"
        with open(self.document_path, "rb") as f:
            raw = f.read()
        if not verify(trusted, raw, sig_row.get("signature", "")):
            return False, ("signature does not verify over current document "
                           "bytes — forged, or document altered after signing")
        return True, "verified"
