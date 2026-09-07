# Sanad — Master Architecture v1.0

**What does Sanad guarantee today, and where do those guarantees end?**

Every section below is an attempt to answer a piece of that question
honestly — with evidence (a passing test, a fixed finding) where the
answer is yes, and with a named gap where it is no. This document is
not a roadmap. Where it describes something Sanad does not yet do,
it says so in [Non-Goals](#10-non-goals) or
[Future Authority Composition](#11-future-authority-composition), never
inside a section that claims present capability.

> **A boundary worth stating up front:** an immutable, hash-chained
> ledger proves that the record is internally *consistent* — that no
> row was silently edited, deleted, or reordered. It does not prove
> the record is *complete*. **Hash-chain consistency ≠ ledger
> completeness.** See [§9](#9-evidence--accountability) for what
> closes that gap and what still doesn't.

---

## 1. Purpose & Boundary

Sanad does not authorize agents to pay. It makes agent authorization
**accountable after the authorization exists**: who acted, under
which mandate, and what happened when the provider went silent.

An AI agent does not hold money or authority. The agent proposes,
Sanad decides — execute or reject — and a `Provider` executes. Sanad
is the layer between "an agent wants to act" and "an action happened
in the real world," responsible for recording, under what authority,
with what outcome.

What Sanad is not is covered explicitly in [§10](#10-non-goals).

---

## 2. Core Authority Model

```
Principal
   ↓
Policy
   ↓
Mandate
   ↓
Agent Identity
   ↓
Action
```

- **Principal** — the human or organization whose money/authority is
  at stake. Signs (or delegates signing of) the policy.
- **Policy / Mandate** — the signed authority document currently
  represented by the mandate/pre-authorization model
  (`validity.py`): `auto_limit_minor`, `daily_budget_minor`,
  `currency`, `blocked_categories`, expiry. The codebase does not
  currently separate "Policy" (the rule) from "Mandate" (the
  specific authorization Sanad enforces) as distinct abstractions —
  this document names both because the conceptual distinction
  matters for future design, not because the code implements it as
  two objects today. Verified against the exact bytes on disk at
  every use; a tampered document invalidates the whole mandate
  (frozen by design — see [§5](#5-current-security-guarantees)).
- **Agent Identity** — an `Ed25519` keypair (`identity.py`). An
  agent's authority to execute can be bound to this key
  (`binding.py`, `BoundGateway`) — see [§5](#5-current-security-guarantees)
  for how completely.
- **Action** — the economic act itself: `item`, `amount_minor`,
  `currency` (the complete field set found in this codebase's
  execution semantics; confirmed no `recipient`/`destination`/
  `purpose`/`account` fields exist anywhere in `Provider.execute()`
  or `ProcurementAgent.propose()`).

Who can grant authority, and how far it can be re-granted to another
agent, is addressed in [§11](#11-future-authority-composition) — that
part of the model does not exist yet.

---

## 3. Authority Lifecycle

```
Proposal
   ↓
Endorsement
   ↓
Authorization
   ↓
Execution
   ↓
Provider Outcome
   ↓
Evidence
```

Each arrow is a place where a value could be substituted for another
without detection. Two of them have been directly attacked:

| Transition | Binding | Status |
|---|---|---|
| Proposal → Endorsement | none found | **Open finding — EXP-015** |
| Endorsement → Authorization | no independent binding | Open, as a consequence of EXP-015 |
| Authorization → Execution | GRANTED-row snapshot, checked before claim/provider call | **Fixed — EXP-014** |
| Execution → Provider Outcome | idempotency key from `execution_id` | Implemented |
| Provider Outcome → Evidence | ledger append + hash chain | Implemented |

---

## 4. Security Invariants

Principles stated as laws that a test can pass or fail:

1. **Payload(execution) = Payload(authorization).**
   Verified — EXP-014 fix. `execute()` rejects any `item`/
   `amount_minor`/`currency` that doesn't match the `GRANTED`
   snapshot, before the claim and before the provider call.

2. **Proposal(endorsed) = Proposal(authorized).**
   **Not enforced.** EXP-015: an endorsement can carry a different
   `amount_minor` or `item` than the proposal it claims to endorse,
   and that different value becomes the authorized (and executed)
   payload.

3. **Authority(child) ⊆ Authority(parent).**
   **Not applicable yet.** No delegation primitive exists to state
   a parent/child relationship over. See [§6](#6-known-architecture-gaps).

4. **Possession(approval) ≠ ExecutionAuthority(caller).**
   Partially true. `BoundGateway` enforces this directly (Agent B
   holding Agent A's approval dict cannot execute it —
   `DENIED_APPROVAL_NOT_YOURS`, `provider.calls=0`). But the
   *default* multi-agent workflow (`tests/test_workflow.py`) wires
   agents through plain `TimedGateway`, where this invariant does
   **not** hold — an unaffiliated gateway sharing only the ledger/
   claims/pre_auth objects can execute anyone's approval.

5. **ExecutionCount(approval) ≤ ClaimPolicy.**
   Verified. `ClaimStore.try_claim(approval_id, execution_id)` is an
   atomic SQLite insert — an approval is consumed exactly once, even
   under real thread races (tested).

---

## 5. Current Security Guarantees

Only what has been directly tested against the real repository.

| Guarantee | Status |
|---|---|
| Authorization → Execution payload binding | **Verified** (EXP-014 fix + regression tests) |
| Replay protection (one approval, one execution) | **Implemented, tested** (`claims.py`, thread-race tested) |
| Agent-key binding of an approval (WHO may execute) | **Implemented** (`binding.py`/`BoundGateway`) — but not wired into the default workflow (see gap below) |
| Provider outcome semantics (EXECUTED / NOT_EXECUTED / UNRESOLVED, never retry-and-hope) | **Implemented** (`reconcile.py`) |
| Mandate tamper-evidence | **Implemented** (`validity.py` — any byte change invalidates the signature) |
| Mandate pinning (a later forged signature can't hijack "current mandate") | **Implemented** (fixed post-EXP-008; pinned to an explicit `pre_auth_id`) |
| Daily budget accounts for settled-late spend | **Implemented** (fixed post live-Stripe finding; derives from execute+resolve rows) |
| Ledger tamper-evidence (edit/delete/reorder detected) | **Implemented** (`chain.py`, hash-chained) |
| Ledger *completeness* (nothing was ever removed) | **Not guaranteed** — a truncated prefix or a full local rewrite by whoever holds the file is undetectable without a published root (`CHECKPOINT.json` is the first step, not a full answer) |
| Proposal → Endorsement payload binding | **Open finding** (EXP-015) |
| Credential Isolation on the default multi-agent path | **Fail** (integration gap — see below) |
| AP2 credential verification | **Not Sanad's job** — Sanad anchors the hash of what was presented; JWT/VC signature verification is the credential provider's responsibility (frozen boundary, `ap2_adapter.py`) |

---

## 6. Known Architecture Gaps

Capabilities that do not exist yet — not broken defenses, absent ones.
Calling these "vulnerabilities" would overstate what was tested.

- **Agent-to-Agent Delegation.** No primitive lets Agent A grant
  part of its authority to Agent B. Searched the full repo; the only
  match for "delegat*" is an unrelated code-delegation comment in
  `api.py`.
- **Delegation attenuation (Authority(B) ≤ Authority(A)).**
  Not testable — there is nothing to attenuate.
- **Tool / resource scope.** A `Gateway` is bound to exactly one
  `Provider` at construction. The only access control is binary
  structural possession of a gateway reference — an agent has full
  access to its one bound provider, or none at all
  (`ProcurementAgent` explicitly "cannot reach a provider at all").
  No allowlist or per-tool permission model exists.
- **Complete agent identity lifecycle.** Key custody, rotation, and
  issuance for agents (and for the operator's own signing key) are
  unanswered — the same class of gap noted since EXP-005.

Not listed here — kept distinct, below, because the mechanism
already exists:

### Integration gap (not an architecture gap)

- **Default-workflow credential isolation.** `BoundGateway` provides
  the required agent-binding mechanism, and it passes its own test
  (Agent B cannot execute Agent A's approval —
  `DENIED_APPROVAL_NOT_YOURS`, `provider.calls=0`). But the default
  multi-agent workflow in `tests/test_workflow.py` wires agents
  through plain `TimedGateway` instead, and therefore does not
  enforce caller-identity binding on that path. A mechanism existing
  but not being wired into the default integration is a different
  class of problem than a mechanism not existing — closing it means
  changing which gateway the workflow uses, not building anything new.

---

## 7. Threat Map (architectural snapshot)

| Capability | Status |
|---|---|
| Mandate → Approval | Implemented |
| Approval → Execution | Implemented, tested |
| Payload binding (Authorization → Execution) | Fixed (EXP-014) |
| Proposal → Endorsement binding | Confirmed finding, open (EXP-015) |
| Agent identity binding | Partial (exists, not universally wired) |
| Credential isolation | Fail (default path) / Pass (`BoundGateway` itself) |
| Agent → Agent delegation | Architecture gap |
| Delegation attenuation A ≤ B | Not testable |
| Tool governance | Architecture gap |
| Tool scope | Not implemented |
| Provider outcome / settlement | Implemented |
| Evidence / ledger | Implemented (consistency, not completeness) |

This table is a snapshot, not a log. The reasoning and reproductions
behind each row live in `REPORT.md`.

---

## 8. Provider Reality Model

Four states that are easy to conflate and must not be:

```
Decision → Execution Attempt → Provider Outcome → Settlement → Reconciliation
```

- **ALLOW ≠ EXECUTED.** A `derive_approval()` grant is a decision,
  not an action. Nothing has touched the real world yet.
- **EXECUTED ≠ SETTLED.** An execution can return successfully while
  the underlying settlement is still pending on the provider's side.
- **UNKNOWN ≠ REFUSED.** If a provider call's response is lost
  (timeout, dropped connection), the outcome is `UNKNOWN` —
  `reconcile.py` asks the provider what actually happened
  (`EXECUTED` / `NOT_EXECUTED` / still `UNRESOLVED`) rather than
  assuming refusal or silently retrying.

This separation is why F3 (the live-Stripe finding) was possible to
name and fix at all: the ledger and the provider can disagree, and
Sanad's job is to notice, not to assume.

---

## 9. Evidence & Accountability

What Sanad can prove after the fact, and what it cannot:

- **Can prove:** the sequence of ledger events is internally
  consistent — no row was silently edited, deleted, or reordered
  without detection (`chain.py`'s hash chain).
- **Cannot prove (yet):** that the ledger is the *complete* history.
  A shorter, honest-looking prefix is a valid chain. A full local
  rewrite by whoever holds the file recomputes every hash correctly.
  **Hash-chain consistency ≠ ledger completeness.** The only answer
  is a checkpoint published somewhere the file's holder does not
  control; `CHECKPOINT.json` is a first signed root, not a durable
  solution — it was sealed with an ephemeral key, which proves the
  root, not a durable identity.
- **Cannot prove:** that a third party's credential (an AP2 mandate)
  was itself valid — only that Sanad anchored the exact bytes it was
  given, plus who acted and what happened next.

---

## 10. Non-Goals

Explicitly out of scope, so Sanad does not quietly expand into
claiming things it was never built or tested to guarantee:

- Not an LLM.
- Not an agent operating system.
- Not an ERP.
- Not a payment provider.
- Not a policy authority (it enforces a policy; it does not decide
  what the policy should be).
- Does not guarantee a `Provider`'s own honesty — only records what
  it said and did.
- Does not claim control over every tool an agent might use — only
  over the single `Provider` a given `Gateway` is bound to.

---

## 11. Future Authority Composition

Not implemented. Recorded here as a design target, deliberately kept
out of every section above that describes present capability.

```
A
│  signed delegation
▼
B
│  attenuated delegation
▼
C
│
▼
Provider
```

**Central law:** `Authority(C) ⊆ Authority(B) ⊆ Authority(A) ⊆ Mandate`

Before any of this is implemented, the plan is design-first, not
code-first:

```
Architecture → Authority Model → Delegation Contract →
Security Invariants → Attack Design → Tests → Implementation
```

Conditions the eventual contract needs to define: scope, expiry,
revocation, budget, tool/resource restrictions, workflow binding,
and delegation depth. The goal is for `Authority(B) ≤ Authority(A)`
to be a testable invariant from the first line of code, not a
diagram arrow retrofitted with tests later.

---

## Sources

Findings, reproductions, and fixes referenced above are logged in
detail in `REPORT.md` at the repository root. This document is the
architectural summary; `REPORT.md` is the evidence.
