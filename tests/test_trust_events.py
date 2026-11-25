# -*- coding: utf-8 -*-
"""RF-009 slice S1 — trust events. Every test cites the decision it enforces
(docs/RF009_DECISION_LOG*.md). Written from the decisions, not from the code.
NOTE: written by the author of the code (Claude); independent review pending.
"""
from sanad.identity import Signer
from sanad.trust_events import (
    TrustState, make_event, fingerprint, canonical, ANY, GRANT, REVOKE,
    TOMBSTONE, APPLIED, DENIED_MALFORMED, DENIED_UNSIGNED,
    DENIED_INVALID_SIGNATURE, DENIED_NOT_TRUST_ADMIN, DENIED_SELF_GRANT,
    DENIED_FP_MISMATCH, DENIED_TOMBSTONED, DENIED_ROOT_KEY, IGNORED_UNSIGNED,
    IGNORED_INVALID_SIGNATURE, IGNORED_UNAUTHORIZED_SIGNER, IGNORED_STALE,
    IGNORED_BAD_EPOCH, IGNORED_ALREADY_ACTIVE, IGNORED_SEQ_GAP,
    IGNORED_ALREADY_REVOKED)

TS = "2026-11-07T00:00:00Z"
P = "acme"


def world():
    ta, key, other = Signer("ta"), Signer("alice-key"), Signer("mallory")
    return ta, key, other, TrustState(ta.public_key_b64), fingerprint(key.public_key_b64)


def grant(signer, key, F, epoch=1, seq=1, principal=P):
    return make_event(signer, GRANT, principal, F, epoch, seq, TS,
                      public_key=key.public_key_b64)


def revoke(signer, F, epoch=1, seq=2, principal=P):
    return make_event(signer, REVOKE, principal, F, epoch, seq, TS)


def tomb(signer, F, seq=1):
    return make_event(signer, TOMBSTONE, ANY, F, 0, seq, TS)


# ---- D-10 clean cutover / D-06 grant authority -----------------------------
def test_state_starts_empty_nothing_is_trusted():  # D-10
    ta, key, other, st, F = world()
    assert not st.is_trusted(P, F) and st.current_epoch(P, F) == 0


def test_ta_grant_opens_epoch_1():  # D-03, D-06
    ta, key, other, st, F = world()
    assert st.apply(grant(ta, key, F)) == APPLIED
    assert st.is_trusted(P, F) and st.current_epoch(P, F) == 1


def test_unsigned_grant_is_denied():  # D-07
    ta, key, other, st, F = world()
    ev = grant(ta, key, F); del ev["signature"]
    assert st.apply(ev) == DENIED_UNSIGNED
    assert not st.is_trusted(P, F) and st.anomalies


def test_tampered_grant_is_denied():  # D-07: signature covers every field
    ta, key, other, st, F = world()
    ev = grant(ta, key, F); ev["body"]["epoch"] = 7
    assert st.apply(ev) == DENIED_INVALID_SIGNATURE
    assert not st.is_trusted(P, F) and st.anomalies


def test_grant_signed_by_a_stranger_is_denied():  # D-06
    ta, key, other, st, F = world()
    assert st.apply(grant(other, key, F)) == DENIED_NOT_TRUST_ADMIN
    assert not st.is_trusted(P, F)


def test_self_grant_is_denied():  # D-06: no self-grant
    ta, key, other, st, F = world()
    assert st.apply(grant(key, key, F)) == DENIED_SELF_GRANT
    assert not st.is_trusted(P, F)


def test_trust_admin_key_cannot_be_granted_through_events():  # D-15
    ta, key, other, st, F = world()
    ev = make_event(ta, GRANT, P, fingerprint(ta.public_key_b64), 1, 1, TS,
                    public_key=ta.public_key_b64)
    assert st.apply(ev) == DENIED_ROOT_KEY


def test_fingerprint_must_match_the_key():  # D-24: trust is bound to (P,F,E)
    ta, key, other, st, F = world()
    ev = make_event(ta, GRANT, P, "0" * 64, 1, 1, TS, public_key=key.public_key_b64)
    assert st.apply(ev) == DENIED_FP_MISMATCH


def test_signature_has_domain_separation():  # D-07 (contract: tagged, canonical)
    ta, key, other, st, F = world()
    ev = grant(ta, key, F)
    ev["signature"] = ta.sign(canonical(ev["body"]))      # no domain tag
    assert st.apply(ev) == DENIED_INVALID_SIGNATURE


def test_malformed_events_are_denied():  # D-07
    ta, key, other, st, F = world()
    ev = grant(ta, key, F); ev["body"]["epoch"] = "1"
    ev2 = grant(ta, key, F); ev2["body"]["seq"] = 0
    assert st.apply(ev) == DENIED_MALFORMED
    assert st.apply(ev2) == DENIED_MALFORMED
    assert st.apply("not an event") == DENIED_MALFORMED


def test_legacy_ledger_rows_are_not_trust():  # D-10: old rows are history only
    ta, key, other, st, F = world()
    legacy = {"stage": "key", "state": "GRANTED", "approver": "owner",
              "public_key": key.public_key_b64}
    legacy_rev = {"stage": "key", "state": "REVOKED", "approver": "owner"}
    assert st.apply(legacy) == DENIED_MALFORMED
    assert st.apply(legacy_rev) == DENIED_MALFORMED
    assert not st.is_trusted(P, F)


# ---- D-03 epochs --------------------------------------------------------
def test_grant_on_active_lifecycle_does_not_open_a_new_epoch():  # D-03.4
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(grant(ta, key, F, epoch=1, seq=2)) == IGNORED_ALREADY_ACTIVE
    assert st.apply(grant(ta, key, F, epoch=2, seq=3)) == IGNORED_BAD_EPOCH
    assert st.current_epoch(P, F) == 1


def test_epoch_numbers_must_follow_without_skipping():  # D-03
    ta, key, other, st, F = world()
    assert st.apply(grant(ta, key, F, epoch=5)) == IGNORED_BAD_EPOCH
    assert not st.is_trusted(P, F)


def test_regrant_after_revoke_opens_a_new_epoch():  # D-03.1
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(ta, F)) == APPLIED
    assert not st.is_trusted(P, F)
    assert st.apply(grant(ta, key, F, epoch=2, seq=3)) == APPLIED
    assert st.current_epoch(P, F) == 2 and st.is_trusted(P, F, 2)


def test_old_epoch_is_never_trusted_again():  # D-03.2, D-03.6
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F)); st.apply(revoke(ta, F))
    st.apply(grant(ta, key, F, epoch=2, seq=3))
    assert not st.is_trusted(P, F, 1) and st.is_trusted(P, F, 2)


def test_regrant_cannot_reuse_the_revoked_epoch():  # D-03: epochs never repeat
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F)); st.apply(revoke(ta, F))
    assert st.apply(grant(ta, key, F, epoch=1, seq=3)) == IGNORED_BAD_EPOCH
    assert not st.is_trusted(P, F)


def test_replaying_the_same_events_is_not_a_grant():  # D-03.5 load != grant
    ta, key, other, st, F = world()
    evs = [grant(ta, key, F), revoke(ta, F), grant(ta, key, F, epoch=2, seq=3)]
    st.replay(evs)
    snap = st.snapshot()
    fresh = TrustState(ta.public_key_b64); fresh.replay(evs)
    assert fresh.snapshot() == snap
    st.replay(evs)                                   # a "reload" of the same log
    assert st.snapshot() == snap and st.current_epoch(P, F) == 2


# ---- D-06 / D-07 revoke -----------------------------------------------------
def test_ta_can_revoke():  # D-06
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(ta, F)) == APPLIED and not st.is_trusted(P, F)


def test_key_owner_can_revoke_its_own_trust():  # D-06
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(key, F)) == APPLIED and not st.is_trusted(P, F)


def test_revoke_by_stranger_is_ignored_with_anomaly():  # D-07
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(other, F)) == IGNORED_UNAUTHORIZED_SIGNER
    assert st.is_trusted(P, F) and st.anomalies


def test_unsigned_revoke_is_ignored_with_anomaly():  # D-07 (decision "ب")
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    ev = revoke(ta, F); del ev["signature"]
    assert st.apply(ev) == IGNORED_UNSIGNED
    assert st.is_trusted(P, F) and st.anomalies


def test_badly_signed_revoke_is_ignored_with_anomaly():  # D-07
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    ev = revoke(ta, F); ev["body"]["ts"] = "2030-01-01T00:00:00Z"
    assert st.apply(ev) == IGNORED_INVALID_SIGNATURE
    assert st.is_trusted(P, F) and st.anomalies


def test_revoke_targets_one_epoch_only():  # D-03/D-04 lifecycle (P,F,E)
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(ta, F, epoch=2)) == IGNORED_BAD_EPOCH
    assert st.is_trusted(P, F)


def test_revoking_one_principal_does_not_touch_another():  # D-06 (P,F,E)
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F, principal="acme"))
    st.apply(grant(ta, key, F, principal="globex"))
    st.apply(revoke(ta, F, principal="acme"))
    assert not st.is_trusted("acme", F) and st.is_trusted("globex", F)


def test_trust_is_bound_to_the_principal():  # D-24
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F, principal="acme"))
    assert not st.is_trusted("globex", F)


# ---- D-07 stale/duplicate -------------------------------------------------
def test_stale_grant_cannot_revive_a_revoked_key():  # D-07
    ta, key, other, st, F = world()
    old = grant(ta, key, F)
    st.apply(old); st.apply(revoke(ta, F))
    assert st.apply(old) == IGNORED_STALE
    assert not st.is_trusted(P, F)


def test_duplicate_revoke_changes_nothing():  # D-07
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    ev = revoke(ta, F)
    st.apply(ev); snap = st.snapshot()
    assert st.apply(ev) == IGNORED_ALREADY_REVOKED and st.snapshot() == snap


# ---- I-05 / I-10: fail-safe asymmetry (implementation choices, owner review) --
def test_revoke_is_never_lost_to_a_stale_sequence_number():  # I-10
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F)); st.apply(grant(ta, key, F, epoch=1, seq=2))
    assert st.apply(revoke(key, F, epoch=1, seq=1)) == APPLIED   # stale seq
    assert not st.is_trusted(P, F)


def test_revoke_applies_even_when_sequence_has_a_gap():  # I-10
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(revoke(ta, F, epoch=1, seq=9)) == APPLIED
    assert not st.is_trusted(P, F)
    assert any(a[0] == "SEQ_GAP" for a in st.anomalies)


def test_grant_with_a_sequence_gap_is_not_applied():  # I-05 fail closed
    ta, key, other, st, F = world()
    assert st.apply(grant(ta, key, F, seq=3)) == IGNORED_SEQ_GAP
    assert not st.is_trusted(P, F) and st.anomalies


def test_gap_on_an_active_key_suspends_it_until_regrant():  # I-05
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(grant(ta, key, F, epoch=1, seq=5)) == IGNORED_SEQ_GAP
    assert not st.is_trusted(P, F)                         # suspended
    assert st.apply(grant(ta, key, F, epoch=2, seq=2)) == APPLIED
    assert st.is_trusted(P, F, 2) and not st.is_trusted(P, F, 1)


# ---- D-05 tombstone ------------------------------------------------------
def test_tombstone_kills_every_epoch_and_principal():  # D-05
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F, principal="acme"))
    st.apply(grant(ta, key, F, principal="globex"))
    assert st.apply(tomb(ta, F)) == APPLIED
    assert not st.is_trusted("acme", F) and not st.is_trusted("globex", F)


def test_tombstoned_fingerprint_can_never_be_granted_again():  # D-05
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F)); st.apply(tomb(ta, F))
    assert st.apply(grant(ta, key, F, epoch=2, seq=3)) == DENIED_TOMBSTONED
    assert not st.is_trusted(P, F)


def test_only_trust_admin_can_tombstone():  # D-05, D-06
    ta, key, other, st, F = world()
    st.apply(grant(ta, key, F))
    assert st.apply(tomb(key, F)).startswith("DENIED")
    assert st.apply(tomb(other, F)).startswith("DENIED")
    assert st.is_trusted(P, F)


def test_trust_admin_key_cannot_be_tombstoned_by_events():  # D-15
    ta, key, other, st, F = world()
    assert st.apply(tomb(ta, fingerprint(ta.public_key_b64))) == DENIED_ROOT_KEY
