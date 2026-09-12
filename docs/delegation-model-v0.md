# Delegation Model v0 — Design, Draft Implementation, and Attack Again

> **Status banner — read first:** this document records a **design-first exploration and draft implementation**, not a production capability. Sanad's production workflow (`sanad/workflow.py`) still has no delegation primitive, and its agents (`FinanceAgent`/`ProcurementAgent`/`AuditAgent`) still have no cryptographic identity at all — confirmed independently three times across this project (the Revocation Consistency Attack's RV-04, Master Architecture §6, and this document's own Layer 3 phase). `sanad/delegation_DRAFT.py` is sandbox-only, was never wired into any production module, and nothing here has been committed as production code.

---

## 1. Why this exists

Master Architecture v1.0 §11 named Agent-to-Agent Delegation as a deliberate, not-yet-implemented design target, with a central law already stated: `Authority(C) ⊆ Authority(B) ⊆ Authority(A) ⊆ Mandate`. This document is the design-first sequence that turns that one-line law into a specified, attacked, and draft-implemented model — following the same discipline established by the Revocation Consistency Attack and EXP-015: **Build → Observe → Attack**, never **Build → Test → Declare Secure**.

## 2. Sequence followed

```
Attack Catalog v0 → Invariants v0 → Four Binding Decisions v0 →
Scope Representation v0.1 → Canonical Object v0 → Signature Semantics v0 →
Test Specifications v0 (TC-DEL-01→20) → Draft Implementation →
20/20 PASS (after 3 corrections found during execution) →
Attack Again (8/8 SAFE) → REPORT.md → Cross-Document Consistency Review
```

Every phase was locked in sequence; no phase was reopened once locked except where a later phase's execution surfaced a genuine contradiction — and every such contradiction is documented below, not smoothed over.

## 3. Attack Catalog v0 — 20 cases, 6 categories

Attenuation (narrowing-only across Scope/Budget/Currency/Expiry/Workflow/Depth), chain identity (lineage vs. content), revocation propagation, budget-as-stock, workflow/tool boundary, and structural chain safety (cycles, self-delegation, depth exhaustion). Reviewed once against prior locked decisions (the Revocation Consistency Attack, EXP-015) with nine corrections applied before the catalog closed — most importantly: **TC-DEL-10 was reframed from "is there a revocation-propagation rule?" (already closed by the Revocation Consistency Attack) to "does chain verification actually enforce it end-to-end?"** — not a rediscovery of settled semantics.

## 4. Invariants v0 — 8 laws, not 20

```
1. Authority(B) ⊆ Authority(A)                    — the core law
2. Per-dimension refinement (Scope/Budget/Currency/Expiry/Workflow/Depth)
3. Lineage, not content — identity ≠ content equivalence
4. Silence is not grant — undefined dimensions infer nothing, in either direction
5. Budget is a stock, not a static ceiling — Original ≥ Consumed + Reserved
6. Revocation propagates down only; effect ≠ authority-to-revoke ≠ identity revocation
7. Grantor ≠ Recipient — standalone, since subset logic alone doesn't block self-delegation
8. Well-founded chains only — a cycle has no valid terminating root
```

**Correction applied before locking:** `workflow_id` and `currency` are single opaque scalars in this model, not sets — `⊆` between two single strings is not well-formed. Both are governed by **exact equality**, not subset, matching how `workflow_id` is actually used in production today (an opaque tracking identifier, not a permission set).

## 5. Four Binding Decisions v0

Locked principle: **the signed object is a Delegation Grant, not a bare Authority object** — it cryptographically binds Grantor (via signature), Recipient (inside the signed bytes), the exact Child Authority, and the specific verified Parent Authority/Delegation it derives from. Root binding reuses the codebase's own existing `pre_auth_id` pinning mechanism rather than inventing a new one. No shortcut field to the root is permitted anywhere in the chain — every hop is walked and independently re-verified.

**The iron separation, carried through every later phase without exception:**
```
Grantor identity        → Signature
Recipient identity      → Signed bytes
Grantor authority       → Parent-chain verification (walked, never shortcut)
Recipient authorization → AuthorizedRecipient(A→B) — OPEN / DEFERRED
Revocation effect       → Separate state
Authority to revoke     → OUT OF MODEL (unchanged from the Revocation Consistency Attack)
```

## 6. Scope Representation v0.1

**A real contradiction was caught here before it became load-bearing.** The first version modeled `Scope` as an absolute exclusion set, which silently required an undefined "universe of actions" and let an empty value mean "unrestricted" — directly violating Invariant 4. **Corrected model:** `scope_delta` — each grant declares only the *additional* restriction it adds; the verifier recomputes `EffectiveRestriction` by walking the whole chain and unioning every hop's contribution, never trusting a stored or claimed value. An empty `scope_delta` at one hop means only "this hop added nothing new" — never "nothing is restricted." The superseded original document is marked accordingly and kept only as a historical record of the error.

## 7. Canonical Object v0

Ten signed fields, closed set (unlike EXP-015, no unsigned sidecar field is permitted at all — an extra field here would risk smuggling what looks like a new authority dimension, not just a misleading note): `type_tag`, `recipient_id`, `parent.kind`/`parent.ref` (a `{kind, ref}` union — `MANDATE` or `DELEGATION`, never a nullable single field), `scope_delta`, `delegatable_budget_minor`, `currency`, `expiry`, `workflow_id`, `remaining_delegation_depth`. Domain-separated with its own type tag (`SANAD-DELEGATION-GRANT-V1`), distinct from EXP-015's two.

`delegation_id = SHA256(canonical_bytes || signature_bytes)`, computed *after* signing — never fed back into what's signed. Including the signature (not just content) in this hash is what correctly distinguishes two different grantors who happen to sign identical content, reusing the exact logic EXP-015's TC-END-19B relied on (Ed25519 determinism).

## 8. Signature Semantics v0

Central rule, sharper than EXP-015's own posture: **"cannot prove authorization ⇒ DENY, never 'probably fine.'"** The verifier's overall result is one of three values — `VALID`, `INVALID`, or **`DEFERRED`** — and `DEFERRED` is a distinct, first-class outcome, never a soft form of `VALID`. Since no mechanism exists today for `AuthorizedGrantor(A, Parent)` or `AuthorizedRecipient(A→B)`, every real (non-test-fixture) verification currently bottoms out at `DEFERRED` by design. The pipeline walks the full lineage hop-by-hop, re-verifying every ancestor independently — no shortcuts, no trusted stored values.

## 9. Test Specifications v0 — TC-DEL-01→20

Written as security properties, not implementation assertions (Precondition/Attack/Expected Decision/Expected Side Effects/Invariant Exercised), under an explicit **Standing Test-Boundary Assumption**: `AuthorizedGrantor`/`AuthorizedRecipient` are supplied as a test fixture for exercising the lineage/attenuation machinery in isolation — never a claim that this authorization exists in production.

## 10. Draft implementation — 20/20 PASS, after three corrections found by execution, not inspection

- **TC-DEL-12** conflated recipient identity with signer identity in its first form; corrected to test that a signer's own revocation blocks that signer's *own further grantor actions*, independent of the parent chain's separate validity.
- **TC-DEL-17** produced a false failure because a static check matched the word "Provider" inside an explanatory *comment*, not functional code; the check was corrected to exclude comments.
- **TC-DEL-20 gave a false PASS on its first run** — self-delegation (`agentA→agentA`) failed for an unrelated reason (the signer wasn't the Mandate's real root signer), never actually exercising self-delegation at all. Rebuilding it with the genuine root signer (`principal→principal`) exposed that **Invariant 7 (`Grantor ≠ Recipient`) was not actually implemented** — a real gap, not a test artifact. Fixed directly in the implementation with an explicit, standalone check, applied recursively to every hop in a chain, not just the leaf.

## 11. Attack Again — 8/8 SAFE, independent of the 20

The strongest result: **A1** built a four-principal, three-hop chain (`principal→A→B→C→D`), revoked the root, and confirmed `B`, `C`, and `D` were all correctly denied via the verifier's *recursive* lineage walk — stronger evidence for revocation propagation than a single-hop test, since it demonstrates the recursion actually recurses. The other seven (self-delegation buried mid-chain, budget double-spend via recipient-name collision, scope-token delimiter injection, depth-equality rejection, negative-depth rejection, workflow case-sensitivity, and an unrelated signer attempting to claim the Mandate root) all held.

## 12. Cross-Document Consistency Review — 3 PASS, 1 CONFLICT found and fixed

The one real conflict: a superseded scope-representation document was still sitting in the file set with no warning it had been replaced — a reader encountering it in isolation would have seen the withdrawn "empty scope = unrestricted" framing with nothing marking it obsolete. Fixed with an explicit superseded banner. One imprecise sentence in this project's own report (claiming a broader runtime guarantee than the tests actually established) was also caught and corrected to state the semantic boundary precisely instead.

## 13. What this establishes — and does not

> **The Draft Delegation Model passed its defined specification and independent Attack Again suite after correcting three test/implementation issues discovered during execution. This does not establish production security: the implementation is not wired into the production workflow, and `AuthorizedGrantor`/`AuthorizedRecipient` remain deferred by design.**

Production's `FinanceAgent`/`ProcurementAgent`/`AuditAgent` still have no cryptographic identity today. `workflow_id` in production still functions only as an opaque tracking thread through Proposal→Endorsement→Approval→Execution, not as any kind of permission scope. Nothing in this document changes either fact.

---

*No production code was committed as part of this experiment. `sanad/delegation_DRAFT.py` remains sandbox-only. This document is a historical record of a design-and-attack cycle, not a changelog entry for a shipped capability.*
