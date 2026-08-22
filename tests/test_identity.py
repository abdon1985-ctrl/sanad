# -*- coding: utf-8 -*-
"""EXP-005 — the signature becomes mathematics.

Frozen expectations:
  S1 honest signature      -> verified, derivation allowed
  S2 typed name, no key    -> DENIED (approver has no trusted key)
  S3 forged: attacker signs with own key claiming to be mohamed -> DENIED (key mismatch)
  S4 tamper after signing  -> DENIED (signature fails over current bytes)
  S5 re-sign after tamper  -> verified again (control returns via a real signature)
  S6 the ledger holds NO private key material anywhere
"""
import json

import pytest

from sanad import Ledger
from sanad.identity import Signer, SignedPreAuthorization, verify


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    doc = tmp_path / "pre_auth.json"
    doc.write_text(json.dumps({"auto_limit_minor": 5000,
                               "daily_budget_minor": 12000,
                               "currency": "USD"}), encoding="utf-8")
    mohamed = Signer("mohamed")
    trusted = {"mohamed": mohamed.public_key_b64}
    pre = SignedPreAuthorization(ledger, str(doc), trusted)
    return ledger, doc, mohamed, trusted, pre


def test_s1_honest_signature_verifies(env):
    ledger, doc, mohamed, trusted, pre = env
    row = pre.sign(mohamed)
    ok, reason = pre.verify_authorization(row)
    assert ok, reason


def test_s2_typed_name_without_key_denied(env):
    """The old world: anyone writes approver='mohamed'. Now it means nothing."""
    ledger, doc, mohamed, trusted, pre = env
    fake_row = {"approver": "ghost", "public_key": "", "signature": ""}
    ok, reason = pre.verify_authorization(fake_row)
    assert not ok and "no trusted key" in reason


def test_s3_forged_identity_denied(env):
    """Attacker generates their OWN key and claims to be mohamed."""
    ledger, doc, mohamed, trusted, pre = env
    attacker = Signer("mohamed")            # same name, different key!
    row = pre.sign(attacker)                 # ledger records attacker's key
    ok, reason = pre.verify_authorization(row)
    assert not ok and "mismatch" in reason   # trusted key wins, name loses


def test_s4_tamper_after_signing_denied(env):
    ledger, doc, mohamed, trusted, pre = env
    row = pre.sign(mohamed)
    terms = json.loads(doc.read_text(encoding="utf-8"))
    terms["auto_limit_minor"] = 999999
    doc.write_text(json.dumps(terms), encoding="utf-8")
    ok, reason = pre.verify_authorization(row)
    assert not ok and "altered" in reason


def test_s5_resign_restores_control(env):
    ledger, doc, mohamed, trusted, pre = env
    row = pre.sign(mohamed)
    terms = json.loads(doc.read_text(encoding="utf-8"))
    terms["auto_limit_minor"] = 3000        # a legitimate change this time
    doc.write_text(json.dumps(terms), encoding="utf-8")
    ok, _ = pre.verify_authorization(row)
    assert not ok                            # old signature dead, correctly
    row2 = pre.sign(mohamed)                 # human re-signs the new document
    ok2, reason2 = pre.verify_authorization(row2)
    assert ok2, reason2


def test_s6_no_private_key_in_ledger(env):
    """The ledger must stay publishable: public keys yes, private never."""
    ledger, doc, mohamed, trusted, pre = env
    pre.sign(mohamed)
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption)
    private_raw = mohamed._private.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    import base64
    private_b64 = base64.b64encode(private_raw).decode()
    ledger_text = open(ledger.path, encoding="utf-8").read()
    assert private_b64 not in ledger_text
    assert mohamed.public_key_b64 in ledger_text   # public IS there


def test_signature_is_byte_exact(env):
    """Same JSON meaning, different bytes -> different document. Same rule
    as the EXP-004 hash: a signature covers bytes, not interpretation."""
    ledger, doc, mohamed, trusted, pre = env
    row = pre.sign(mohamed)
    terms = json.loads(doc.read_text(encoding="utf-8"))
    doc.write_text(json.dumps(terms, indent=2), encoding="utf-8")  # same meaning!
    ok, _ = pre.verify_authorization(row)
    assert not ok
