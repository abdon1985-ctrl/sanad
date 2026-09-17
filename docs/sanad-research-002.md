# SANAD-RESEARCH-002 — Testing Sanad's Model Against Broader Agentic-AI Security Properties

> **Status banner — read first:** this document records a **research phase** — three closed investigations, one of them a confirmed **Security Finding**. **No code has been changed as a result of this research.** RF-009's finding is documented here as a *finding only*; remediation is explicitly deferred to a separate, later, independently-named phase (RF-009 Remediation Research), starting from a semantic freeze before any implementation — not a same-session patch.

---

## 1. Why this research exists

After closing Delegation Model v0 with a NO-GO Production Integration verdict, the natural next question was not "what do we build next" but a deeper one: **does Sanad's current model have security properties from the broader agentic-AI space that it doesn't yet claim, represent, or enforce at all?** This phase tests that question directly, under one governing rule stated up front and held to throughout:

> No Feature is added because the market has one. No absence of a feature is called a vulnerability. A finding requires all three: an actual Sanad claim, a representable minimal attack, and a confirmed bypass of an existing control — otherwise the honest classification is GAP, INFRASTRUCTURE GAP, or OUT OF MODEL, never "Security Finding" by default.

**Pipeline applied to every RF item:** Threat Model → Minimal Attack → Required Information → Does Sanad claim it? → Does the model represent it? → Can enforcement be bypassed? → Classification.

## 2. RF-006 — Authorized Action ≠ Authorized Sequence

**Result: GAP + INFRASTRUCTURE GAP — not a Security Finding.**

**Minimal attack:** the same business request (e.g., "vendor invoice #123, $40,000") is proposed twice. Each proposal independently derives a *different*, individually-valid approval and executes successfully — the vendor is paid twice. Every single Sanad invariant holds throughout (payload binding, per-approval replay protection, daily budget, currency, category limits) — nothing was individually violated.

**Why not a Finding:** Master Architecture §5's own guarantee table states the claim precisely — *"Replay protection (**one approval, one execution**)"* — scoped to a single approval artifact, never to business-request-level uniqueness. A full repository-wide search (corrected after an initial narrower check was rightly challenged) found an existing `Idempotency-Key` mechanism, but it is scoped to `execution_id` — network-retry safety for one execution, not deduplication across two independently-derived approvals. Strongest confirmation: `tests/test_api.py::test_api_http_replay_creates_new_transaction` already reproduces this exact scenario through the real HTTP API, and its own docstring states the accepted result plainly: *"Same HTTP body twice = two distinct transactions (no idempotency)."* RF-006 independently confirmed a boundary the project had already named and tested — not a hidden oversight.

**One-line summary:** Sanad protects replay at the execution-artifact level; it does not define business-level uniqueness across independently authorized approvals.

## 3. RF-009 — Execution-Time Revocation Failure

**Result: SECURITY FINDING 🔴, confirmed on the mandate-signer path across every production execution entry point.**

**Core finding, locked wording:**
> A previously granted approval remains executable after the authority-signing key has been revoked, because execution does not re-evaluate the current revocation state required by the normative execution-eligibility model.

**Empirically reproduced** (not just reasoned about) against the real `TimedGateway`/`TrustedKeys`/`TimedSignedPreAuthorization` classes:
```
t1: derive_approval() while trusted  →  GRANTED
t2: keys.revoke(signer, "device reported stolen")
t3: execute(the approval from t1)    →  EXECUTED, provider.calls == 1
```

**Why this is a Finding, not a GAP:** the normative "Revocation Authority Semantics v1.0" specification — already used as authoritative throughout the closed Revocation Consistency Attack — states, in §3, that `NotRevoked` is a conjunct of `ExecutionEligible`, and in §18 (LOCKED): *"Revocation is immediate for future execution."* This is a claim Sanad already made. It does not hold.

**Precisely distinguished from RV-K1 (closed, unaffected, still holds):** an isolating test confirmed that a *new* derivation attempt after revocation is correctly `DENIED` (RV-K1's exact, still-valid scenario) — while *execution* of an approval granted *before* revocation still succeeds (RF-009's distinct scenario). RV-K1 tested `revoke → derive`; RF-009 tests `derive → revoke → execute`. Different attack surface, not a reopened or contradicted finding.

**Scope confirmed across the full codebase:** `execute()` in both `sanad/gateway.py` and `sanad/gateway_timed.py` checks only payload-match (EXP-014) and claim uniqueness — `TrustedKeys.is_revoked()`/`pre_auth.verify_authorization()` exist *only* inside `derive_approval()`. Every production execution entry point (`sanad/api.py`, `sanad_mcp/server.py`, `wakeel/wakeel_agent.py`) routes through this same core with no additional check.

**`BoundGateway` (EXP-009's agent-binding layer) also empirically confirmed vulnerable** — but its remediation shape differs: the bound agent's key was found to never be a `TrustedKeys` member at all, so its own proof-of-possession check has no connection point to the revocation registry to check even in principle. This intersects an *already-declared* EXP-009 gap (agent key custody, named in that module's own docstring) rather than being a second, new finding — explicitly not double-counted.

**No fix proposed. No code touched. No architecture change made.** Remediation is deliberately deferred to its own phase (see §5).

## 4. RF-007 — Authorization ≠ Independent Proof of Execution

**Result: OUT OF MODEL.**

**Question tested:** does Sanad claim its `EXECUTED` ledger state constitutes independent proof an action happened in the real world, beyond recording what the Provider reported?

**Resolved quickly and cleanly:** Master Architecture v1.0 §10 (Non-Goals), already live on the real repository, states explicitly: *"Does not guarantee a Provider's own honesty — only records what it said and did."* This is the same boundary already established for AP2 credentials (F5: anchored, not verified). Confirmed via `sanad/providers/stripe.py` that the Provider integration is a standard authenticated API call trusting the SDK's own response — consistent with the stated disclaimer, not a broken promise. No claim existed to bypass; classified OUT OF MODEL rather than GAP, because a GAP is an unreasoned-about property, and this one was explicitly reasoned about and excluded, in writing, before this research phase began.

## 5. What happens next — and what does not

```
RF-006  Authorized Action ≠ Authorized Sequence          → GAP + INFRASTRUCTURE GAP
RF-009  Execution-Time Revocation Failure                → SECURITY FINDING 🔴
RF-007  Authorization ≠ Independent Proof of Execution   → OUT OF MODEL
```

**RF-009's remediation is intentionally a separate, later, independently-documented phase — "RF-009 Remediation Research" — not started here.** Its agreed opening step, when begun, is a **Phase 0: Semantic Freeze**, resolving eight questions before any implementation: the exact meaning of "current" in `NotRevoked`, the authoritative revocation-state source, when execution must observe it, its relationship to `TimedGateway`'s existing TTL model, behavior under concurrent revoke/execute races, behavior for already-claimed vs. not-yet-claimed approvals, testing the proposed semantics against both `Gateway` and `BoundGateway`, and — critically — not pre-selecting *how* "current" is checked (every `execute()` call, every claim, a bounded freshness window, or another explicit semantic) before that question is deliberately resolved on its own. This mirrors the exact discipline that caught real defects late in both EXP-015 (TC-END-19) and Delegation Model v0 (TC-DEL-20): a fix that looks obviously correct in code can still rest on an unresolved semantic question.

---

*No code was changed as part of this research phase. This document is a historical record of three closed investigations, not a changelog entry for a shipped fix.*
