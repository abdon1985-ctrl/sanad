# -*- coding: utf-8 -*-
"""Signed pre-authorization — autonomy is a pre-signed approval, not absent approval.

Rules proven in EXP-002 and EXP-004:
- The authorization is a *document*: raw bytes are read once, and both the JSON
  terms and the hash are derived from those same bytes (no TOCTOU window).
- Signing snapshots the full terms into the ledger. Later decisions read terms
  from the ledger snapshot; the file on disk is only checked for hash equality.
- Any byte-level change after signing invalidates the WHOLE document
  (PRE_AUTH_HASH_MISMATCH) — even for operations that would be allowed under
  both versions. A signature covers a document, not clauses.
"""
import hashlib
import json
import uuid

from .ledger import Ledger


def read_document(path: str):
    """Read the pre-auth file ONCE; derive terms and hash from the same bytes."""
    with open(path, "rb") as f:
        raw = f.read()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return json.loads(raw.decode("utf-8")), digest


class PreAuthorization:
    def __init__(self, ledger: Ledger, document_path: str):
        self.ledger = ledger
        self.document_path = document_path

    def sign(self, approver: str) -> dict:
        """A human's explicit act. Snapshots terms + hash into the ledger."""
        terms, digest = read_document(self.document_path)
        pre_auth_id = "PRE-AUTH-" + uuid.uuid4().hex[:8]
        return self.ledger.append(
            "pre_auth", "SIGNED",
            f"{pre_auth_id} by {approver} — hash {digest}",
            pre_auth_id=pre_auth_id, pre_auth_hash=digest,
            approver=approver, terms=terms)

    def current_signature(self):
        """Latest SIGNED entry from the ledger — the ledger is the reference."""
        return self.ledger.last(stage="pre_auth", state="SIGNED")

    def verify_untampered(self, signature: dict):
        """Compare the file's current bytes against the signed hash.
        Returns (ok, current_hash)."""
        _, current = read_document(self.document_path)
        return current == signature["pre_auth_hash"], current
