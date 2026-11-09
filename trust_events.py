# -*- coding: utf-8 -*-
"""Sanad trust events — signed GRANT / REVOKE / TOMBSTONE and the state they define.

RF-009, slice S1. Implements decisions D-03, D-05, D-06, D-07, D-10, D-15, D-24
of docs/RF009_DECISION_LOG*.md. Storage-agnostic on purpose: this module is a
pure replay of events; WHERE events are stored is still an open decision.

Rules (each is a decision, not an implementation whim):
- Trust is the binding (P, F, E): signatures of key F are accepted as speaking
  for principal P during epoch E. It is NOT authority (D-24).
- The root of trust is the Trust Administrator's key, supplied from outside
  (configuration), never granted through events (D-15).
- GRANT and TOMBSTONE: Trust Administrator signature only (D-06).
  REVOKE: Trust Administrator OR the key's own signature (D-06).
- Unsigned or badly signed GRANT/TOMBSTONE is DENIED; an unsigned or badly
  signed REVOKE is IGNORED. Both leave state unchanged and are recorded as
  anomalies (D-07).
- Old or duplicate events change nothing (D-07). A re-grant after a revoke
  opens a NEW epoch; epochs never repeat (D-03).
- FAIL-SAFE ASYMMETRY (implementation choice I-10): events that REDUCE trust
  (REVOKE, TOMBSTONE) apply whenever authentic and aimed at the current epoch,
  even if their sequence number is stale or has a gap — a revocation must never
  be lost to bookkeeping. Events that EXPAND trust (GRANT) must be fresh and
  gapless; a gap fails closed: the grant is not applied and an active key is
  suspended until the Trust Administrator re-grants (I-05).
- TOMBSTONE is final for a fingerprint (D-05).
- Nothing is imported from legacy ledger rows: state starts empty (D-10).
- Loading state is replay, not a grant: replaying the same events twice gives
  the same state and creates no epoch (D-03.5).

What this module does NOT do: it does not prove completeness of the event log
(a deleted tail event is invisible — D-08, a declared v1 limit), and it does
not bind approvals to epochs (slice S2).
"""
import base64
import hashlib
import json

from .identity import verify

DOMAIN = b"sanad/trust-event/v1\x00"
GRANT, REVOKE, TOMBSTONE = "GRANT", "REVOKE", "TOMBSTONE"
ANY = "*"                      # principal used by TOMBSTONE (global per key)

APPLIED = "APPLIED"
DENIED_MALFORMED = "DENIED_MALFORMED"
DENIED_UNSIGNED = "DENIED_UNSIGNED"
DENIED_INVALID_SIGNATURE = "DENIED_INVALID_SIGNATURE"
DENIED_NOT_TRUST_ADMIN = "DENIED_NOT_TRUST_ADMIN"
DENIED_SELF_GRANT = "DENIED_SELF_GRANT"
DENIED_FP_MISMATCH = "DENIED_FP_MISMATCH"
DENIED_TOMBSTONED = "DENIED_TOMBSTONED"
DENIED_ROOT_KEY = "DENIED_ROOT_KEY_NOT_GRANTABLE"
IGNORED_UNSIGNED = "IGNORED_UNSIGNED"
IGNORED_INVALID_SIGNATURE = "IGNORED_INVALID_SIGNATURE"
IGNORED_UNAUTHORIZED_SIGNER = "IGNORED_UNAUTHORIZED_SIGNER"
IGNORED_STALE = "IGNORED_STALE"
IGNORED_SEQ_GAP = "IGNORED_SEQ_GAP"
IGNORED_BAD_EPOCH = "IGNORED_BAD_EPOCH"
IGNORED_ALREADY_ACTIVE = "IGNORED_ALREADY_ACTIVE"
IGNORED_ALREADY_REVOKED = "IGNORED_ALREADY_REVOKED"
IGNORED_ALREADY_TOMBSTONED = "IGNORED_ALREADY_TOMBSTONED"
IGNORED_UNKNOWN_KEY = "IGNORED_UNKNOWN_KEY"

# outcomes that are security-relevant (recorded as anomalies)
_PLAIN = {APPLIED, IGNORED_STALE, IGNORED_ALREADY_ACTIVE,
          IGNORED_ALREADY_REVOKED, IGNORED_ALREADY_TOMBSTONED}


def fingerprint(public_key_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def message(body: dict) -> bytes:
    """Bytes actually signed: domain tag + canonical body. The tag keeps a
    trust-event signature from being valid in any other signing context."""
    return DOMAIN + canonical(body)


def make_event(signer, typ, principal, fp, epoch, seq, ts, public_key=None):
    body = {"v": 1, "type": typ, "principal": principal, "fingerprint": fp,
            "epoch": epoch, "seq": seq, "ts": ts,
            "signer_fp": fingerprint(signer.public_key_b64)}
    if typ == GRANT:
        body["public_key"] = public_key
    return {"body": body, "signature": signer.sign(message(body))}


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


class TrustState:
    """State defined by an ordered sequence of trust events."""

    def __init__(self, root_public_key_b64: str):
        self._root_pub = root_public_key_b64
        self.root_fp = fingerprint(root_public_key_b64)
        self._keys = {}          # (P, F) -> {pub, epoch, active, last_seq}
        self._tomb = {}          # F -> seq of tombstone event
        self.log = []            # (outcome, type, principal, fingerprint)
        self.anomalies = []      # (code, detail)

    # ---- queries ------------------------------------------------------
    def current_epoch(self, principal, fp) -> int:
        e = self._keys.get((principal, fp))
        return e["epoch"] if e else 0

    def is_trusted(self, principal, fp, epoch=None) -> bool:
        if fp in self._tomb:
            return False
        e = self._keys.get((principal, fp))
        if not e or not e["active"]:
            return False
        return epoch is None or epoch == e["epoch"]

    def public_key(self, principal, fp):
        e = self._keys.get((principal, fp))
        return e["pub"] if e else None

    def snapshot(self) -> dict:
        return {"keys": {f"{p}|{f}": [e["epoch"], e["active"], e["last_seq"]]
                         for (p, f), e in sorted(self._keys.items())},
                "tombstoned": sorted(self._tomb)}

    # ---- replay ---------------------------------------------------------
    def replay(self, events):
        return [self.apply(e) for e in events]

    def apply(self, event) -> str:
        body = self._parse(event)
        if body is None:
            return self._done(DENIED_MALFORMED, event, "malformed event")
        sig = event.get("signature")
        typ = body["type"]
        strict = typ in (GRANT, TOMBSTONE)      # strict types are DENIED
        pub = self._signer_pub(body)
        # 1. who is allowed to speak for this event type
        if typ in (GRANT, TOMBSTONE):
            if body["signer_fp"] != self.root_fp:
                code = (DENIED_SELF_GRANT
                        if body["signer_fp"] == body["fingerprint"]
                        else DENIED_NOT_TRUST_ADMIN)
                return self._done(code, event, "signer is not Trust Administrator")
        elif pub is None:
            return self._done(IGNORED_UNAUTHORIZED_SIGNER, event,
                              "revoke signer is neither admin nor the key itself")
        # 2. signature
        if not isinstance(sig, str) or not sig:
            return self._done(DENIED_UNSIGNED if strict else IGNORED_UNSIGNED,
                              event, "no signature")
        if not verify(pub, message(body), sig):
            return self._done(DENIED_INVALID_SIGNATURE if strict
                              else IGNORED_INVALID_SIGNATURE, event,
                              "signature does not verify")
        # 3. meaning
        if typ == GRANT:
            return self._grant(body, event)
        if typ == REVOKE:
            return self._revoke(body, event)
        return self._tombstone(body, event)

    # ---- internals ------------------------------------------------------
    def _parse(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("body"), dict):
            return None
        b = event["body"]
        ok = (b.get("v") == 1 and b.get("type") in (GRANT, REVOKE, TOMBSTONE)
              and isinstance(b.get("principal"), str)
              and isinstance(b.get("fingerprint"), str)
              and len(b["fingerprint"]) == 64
              and _is_int(b.get("epoch")) and b["epoch"] >= 0
              and _is_int(b.get("seq")) and b["seq"] >= 1
              and "ts" in b and isinstance(b.get("signer_fp"), str))
        if ok and b["type"] == GRANT:
            ok = isinstance(b.get("public_key"), str)
        if ok and b["type"] == TOMBSTONE:
            ok = b["principal"] == ANY
        return b if ok else None

    def _signer_pub(self, body):
        if body["signer_fp"] == self.root_fp:
            return self._root_pub
        if body["type"] == REVOKE and body["signer_fp"] == body["fingerprint"]:
            e = self._keys.get((body["principal"], body["fingerprint"]))
            return e["pub"] if e else None
        return None

    def _done(self, code, event, detail=""):
        b = event.get("body", {}) if isinstance(event, dict) else {}
        b = b if isinstance(b, dict) else {}
        self.log.append((code, b.get("type"), b.get("principal"),
                         b.get("fingerprint")))
        if code not in _PLAIN:
            self.anomalies.append((code, detail))
        return code

    def _grant(self, body, event):
        p, f = body["principal"], body["fingerprint"]
        if f == self.root_fp:
            return self._done(DENIED_ROOT_KEY, event, "root key is not granted by events")
        if fingerprint(body["public_key"]) != f:
            return self._done(DENIED_FP_MISMATCH, event, "fingerprint != key")
        if f in self._tomb:
            return self._done(DENIED_TOMBSTONED, event, "tombstoned fingerprint")
        e = self._keys.get((p, f))
        last = e["last_seq"] if e else 0
        if body["seq"] <= last:
            return self._done(IGNORED_STALE, event)
        if body["seq"] > last + 1:                  # I-05: fail closed
            if e and e["active"]:
                e["active"] = False                 # suspended until re-grant
            return self._done(IGNORED_SEQ_GAP, event,
                              f"grant seq {body['seq']} after {last}")
        if e and e["active"]:
            if body["epoch"] != e["epoch"]:
                return self._done(IGNORED_BAD_EPOCH, event, "grant on active lifecycle")
            e["last_seq"] = body["seq"]
            return self._done(IGNORED_ALREADY_ACTIVE, event)
        expected = (e["epoch"] if e else 0) + 1
        if body["epoch"] != expected:
            return self._done(IGNORED_BAD_EPOCH, event, f"expected epoch {expected}")
        self._keys[(p, f)] = {"pub": body["public_key"], "epoch": expected,
                              "active": True, "last_seq": body["seq"]}
        return self._done(APPLIED, event)

    def _revoke(self, body, event):
        p, f = body["principal"], body["fingerprint"]
        e = self._keys.get((p, f))
        if not e:
            return self._done(IGNORED_UNKNOWN_KEY, event, "revoke of unknown (P,F)")
        if body["epoch"] != e["epoch"]:
            return self._done(IGNORED_BAD_EPOCH, event, "revoke targets another epoch")
        if body["seq"] > e["last_seq"] + 1:         # I-10: record, never refuse
            self.anomalies.append(("SEQ_GAP", f"revoke seq {body['seq']} after {e['last_seq']}"))
        e["last_seq"] = max(e["last_seq"], body["seq"])
        if not e["active"]:
            return self._done(IGNORED_ALREADY_REVOKED, event)
        e["active"] = False
        return self._done(APPLIED, event)

    def _tombstone(self, body, event):
        f = body["fingerprint"]
        if f == self.root_fp:
            return self._done(DENIED_ROOT_KEY, event, "cannot tombstone the root key")
        if f in self._tomb:
            return self._done(IGNORED_ALREADY_TOMBSTONED, event)
        self._tomb[f] = body["seq"]
        for (p, kf), e in self._keys.items():
            if kf == f:
                e["active"] = False
        return self._done(APPLIED, event)
