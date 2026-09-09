# EXP-015 — Proposal → Endorsement Binding (Design + Draft Implementation, v0)

> **Status banner — read first:** F7 (Proposal → Endorsement Binding Failure) **remains OPEN in production.** This document records a verified *draft* design and implementation that was never wired into `sanad/workflow.py` or any other production module, and was never committed as production code. "VERIFIED" below describes the draft's own test results, not a production fix. The original F7 finding remains live in the real codebase: amount and item tampering remain executable, while the currency case is denied incidentally by existing pre-authorization policy rather than by proposal→endorsement binding.

---

## 1. Baseline — the original finding (F7)

`FinanceAgent.endorse()` records whatever payload it's given, with no comparison against the original `ProcurementAgent` proposal before `carry_to_sanad()` builds an authorization. Reproduced against the real repo:

| Attack | Result |
|---|---|
| Amount tampered 500→999999 | EXECUTED, caught only post-hoc by `AuditAgent`'s `ENDORSEMENT_EXCEEDS_PROPOSAL` heuristic (a report, not a block) |
| Item tampered laptop→server-rack | EXECUTED, **zero audit detection** — the heuristic only checks amount |
| Currency tampered USD→EUR | DENIED, but incidentally — from `derive_approval()`'s general pre-auth currency policy, not from any proposal-vs-endorsement comparison |

## 2. Design sequence followed (Build → Observe → Attack, not Build → Test → Declare Secure)

```
Attack Catalog v0 → Invariants → Canonical Object v0 → Layer 3 Authority Gap →
Signature Semantics v0 → Test Specifications (TC-END-01→20) → Draft Implementation →
Run Tests → Attack Again → REPORT.md → Cross-Document Consistency Review → 3-Gate Closure
```

### Attack Catalog v0 — 20 cases, 6 categories
Single-field substitution, cross-proposal reattachment, multi-field combinations, structural/malformed input, replay/ordering, and persuasion/social-engineering framing. Full catalog and decisions available on request; the two invariants it produced:

**Invariant 1 (Binding):** An endorsement is valid only for the exact proposal identity and canonical security content it endorses. Any difference in `workflow_id`, `proposal_id`, `item`, `amount_minor`, or `currency` invalidates it. Symmetric — a "smaller" deviation is still a deviation.

**Invariant 2 (Single authoritative endorsement):** A proposal must not have multiple simultaneously authoritative endorsements. A later endorsement never silently replaces an existing one.

### Canonical Object v0
Non-JSON, fixed-delimiter (`0x1F`) canonical byte string — deliberately not JSON canonicalization, matching the project's existing raw-byte-signing style (`validity.py`). Domain-separated with a type tag (`SANAD-ENDORSEMENT-V1` vs `SANAD-PROPOSAL-V1`) to prevent cross-type signature confusion. `proposal_id`/`workflow_id` are inside the signed bytes — this is what makes identity, not content, the thing verified (see TC-END-07 below). `amount_minor` must be a real `int` (bool explicitly excluded); `item` is NFC-normalized; unsigned metadata (`note`) is structurally outside the signed payload, not merely "ignored by convention."

### Layer 3 — Endorsement Authority (a pre-existing, declared gap, not solved here)
`FinanceAgent`/`ProcurementAgent`/`AuditAgent` have **zero cryptographic identity** in production today — `name="finance"` is a bare string. `workflow.py`'s own docstring already states: *"anyone able to append to this ledger can write an endorsement row."* This is an **Architecture Gap**, joining Delegation A≤B and Tool Governance in the existing gap list — not an EXP-015 design question, and not resolved by it.

**Explicit boundary carried through every document and every test:**
```
TrustedSigner(E)              AuthorizedFinanceAgent(E, P)
        |                              |
        v                              v
cryptographic prerequisite    future architecture — NOT implemented,
(key registered, not          NOT tested, NOT claimed here
 revoked)
```

### Signature Semantics v0 — the verifier pipeline
Central correction this phase made: **signature correctness ≠ binding correctness.** The verifier must reconstruct canonical bytes **from the stored proposal**, never trust the endorsement's own claimed copy — this single rule is what makes cross-proposal and identical-content attacks (below) actually fail, not just "discouraged by design."

```
Endorsement Envelope → Structural validation → Proposal lookup by proposal_id →
Rebuild canonical object FROM STORED PROPOSAL → Verify signature → TrustedSigner(E) →
Existing authoritative endorsement? → [no: ACCEPT] / [yes, same artifact: IDEMPOTENT] / [yes, different: CONFLICT/DENY]
```

## 3. Draft implementation and results

Implemented as a standalone, additive module (`sanad/exp015_binding_DRAFT.py` in the working sandbox) — never imported by, or wired into, any production file.

**TC-END-01 → TC-END-20: 21/21 PASS** (TC-END-19 split into 19A/19B per spec).

**A specification error was found and corrected by actually running the tests, not by inspection beforehand:** TC-END-19's original framing (a second endorsement with a *different, more conservative amount* reaching the CONFLICT check) is structurally impossible once Invariant 1 exists — any content-mismatched endorsement is already denied by Invariant 1 before Invariant 2 is ever reached. The corrected, actually-reachable construction: a second endorsement with **content identical** to the stored proposal, signed by a **different trusted key** (the only way to get a distinct-but-validly-bound artifact under deterministic Ed25519 signing). The Attack Catalog, Test Specifications, and this closure were all corrected to reflect this — the denial outcome was never wrong, only the internal classification of *which* invariant produces it.

**Attack Again — 9 independently-designed attempts, separate from the authored 20: 9/9 safe, zero confirmed bypasses.** One documentation correction arose: a revoked signer's endorsement is correctly denied, but via `sanad.validity.TrustedKeys.get()` returning `None` for revoked names at the key-lookup step — the pipeline's own explicit `is_revoked` check is redundant on this specific retrieval path, not an independent enforcement. The security result (denial) was never in question; only which line performs it.

## 4. Gate 2 — final adversarial review: is there a second path to endorsement acceptance?

Searched the entire real repository (`sanad/`, `sanad_mcp/`, `wakeel/`, `demos/`, `tests/`) for `proposal_id`, `endorse`, `finance_signature`, `canonical`, `authoritative`, `TrustedSigner`, outside the draft module.

**Finding: there is no second path — because there is no production path at all.** `proposal_id` has zero occurrences anywhere in production code. `sanad/api.py` bypasses this entire layer, calling `derive_approval()`/`execute()` directly (EXP-014's territory). `sanad_mcp/server.py`, `wakeel/*.py`, `demos/*.py` contain none of the searched terms. The one incidental match (`reconcile.py`'s use of "authoritative" for a settlement record) was checked and confirmed unrelated. This means the original F7 vulnerability remains present in production today: amount and item tampering remain executable, while the currency case is still denied incidentally by the existing pre-authorization currency policy rather than by proposal→endorsement binding. Nothing from this experiment was ever wired into production.

## 5. Final classification (Gate 3)

```
EXP-015
Status:      VERIFIED   (within the stated draft scope: binding, canonicalization,
                          signature validation, single-authoritative-endorsement)
New Finding: NONE

AuthorizedFinanceAgent(E,P)  →  Architecture Gap  →  NOT resolved by EXP-015
```

**"VERIFIED" and "New Finding: NONE" describe the draft's own tested design — not production.** F7 remains logged as OPEN until this design (or one like it) is actually integrated into `sanad/workflow.py`.

---

*No production code was committed as part of this experiment. `sanad/exp015_binding_DRAFT.py` remains sandbox-only. This document is a historical record of the design-and-attack cycle, not a changelog entry for a shipped fix.*
