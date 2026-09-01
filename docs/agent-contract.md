# Sanad Agent Contract: Current Implementation

**Version:** Derived from Sanad main @ 2bc12a0  
**Scope:** Describes the current implementation boundary of Sanad. This is not yet a versioned public protocol or SDK contract.  
**Principle:** The code determines the truth; the document describes the truth, not the reverse.

---

## 1. Purpose

This document extracts the minimal interface that any external agent must satisfy to interact with Sanad's current architecture. It is derived from what Sanad *actually validates today*, not from an idealized protocol.

---

## 2. Current Sanad Interface

### Stage A: Proposal
**Who:** Any process with write access to the ledger.  
**Required fields:**
- `item` (string)
- `amount_minor` (int)
- `currency` (string)

**Optional metadata:**
- `justification` (string) — human-readable reason; not used for authorization
- `workflow_id` (string) — if omitted, Sanad generates one

**What Sanad does:** Records the proposal in the ledger. No authorization validation occurs at this stage.

### Stage B: Endorsement
**Who:** Any process with write access to the ledger.  
**Required:** The full proposal object from Stage A.  
**Optional metadata:**
- `note` (string) — endorser's opinion; not used for authorization

**What Sanad does:** Records the endorsement in the ledger. No authorization validation occurs at this stage.

**Known limitation:** Endorsements are **not cryptographically signed**. They are ledger claims, not proof of identity. Sanad re-checks the PreAuthorization independently during execution, so a forged endorsement buys no execution authority, but the row itself remains visible testimony.

### Stage C: Execution Request
**Who:** The process holding the gateway instance (`TimedGateway`).  
**Required fields:**
- `item` (string)
- `amount_minor` (int)
- `currency` (string)

**What Sanad does:**
1. Verifies the PreAuthorization document signature exists and is valid
2. Verifies the signer's key is not revoked
3. Verifies the document has not expired (`valid_days`)
4. Verifies `amount_minor` ≤ `auto_limit_minor`
5. Verifies cumulative amount ≤ `daily_budget_minor`
6. Verifies currency matches
7. Verifies item is not in `blocked_categories`
8. Derives an `approval_id`
9. If `approval_ttl_seconds` is set: verifies approval is not stale
10. If all pass: calls the provider to execute

**Return:**
- `row` dict with `state == "EXECUTED"` — success
- `None` — refused (reason recorded in ledger as `REFUSED_BY_SANAD`)

---

## 3. What Sanad Actually Validates

Sanad does not validate the agent's identity cryptographically. It validates:

1. **PreAuthorization Document** — cryptographically signed, not expired, key in standing
2. **Terms within that document** — `auto_limit_minor`, `daily_budget_minor`, `blocked_categories`, `currency`
3. **Approval freshness** — if `approval_ttl_seconds` is set

The agent's name appears in the ledger, but Sanad's execution decision is based solely on the PreAuthorization document, not on who proposed or endorsed.

---

## 4. Agent-Side Requirements

To be governable by Sanad, an agent SHOULD:

1. **Produce structured proposals** with the 3 required fields (`item`, `amount_minor`, `currency`)
2. **Identify itself in ledger records** for auditability. This identity is **not cryptographically authenticated** by the current architecture.
3. **Route execution through a Sanad gateway** rather than holding direct provider access

An agent MUST NOT, in a governed integration:

1. **Have an independent path to provider execution credentials** — if the agent can call the provider directly, Sanad is bypassed entirely. This is an **integration invariant**, not currently enforced by Sanad itself.
2. **Modify the PreAuthorization document** — the signature will fail verification
3. **Expect its endorsement to grant authority** — Sanad re-checks everything from the signed document

---

## 5. Security Boundaries

### What Sanad Guarantees
- **No execution without valid PreAuthorization** — missing, expired, tampered, or revoked-key documents result in denial
- **No execution beyond terms** — `auto_limit` and `daily_budget` are enforced structurally, not interpreted
- **No NL-based authorization** — Sanad does not use natural-language `justification` or `note` text as an authorization signal. EXP-013-W9 demonstrated that prompt-level manipulation does not change the authorization inputs Sanad evaluates.
- **Tamper-evident chained audit trail** — every proposal, endorsement, approval, refusal, and execution is recorded in the ledger with `workflow_id` threading

### What Sanad Does NOT Guarantee
- **Agent identity verification** — who proposes or endorses is recorded but not cryptographically proven
- **Communication security** — Sanad does not inspect emails, web traffic, or voice commands
- **General OS security** — Sanad is not a sandbox; it is an authorization gate for sensitive execution
- **Credential isolation enforcement** — Sanad cannot prevent an agent from holding provider credentials outside its scope

---

## 6. Known Limitations

| Limitation | Impact | Status |
|---|---|---|
| **Unsigned endorsements** | Anyone with ledger write access can forge an endorsement row | Sanad still refuses execution (terms check), but the forgery is visible testimony |
| **Agent key custody** | No cryptographic proof that Procurement or Finance is who they claim | Architecture question: should agents have their own signing keys? |
| **Non-financial tool governance** | Sanad only governs `TimedGateway`. Other tools are invisible | Architecture gap: Tool Governance Model |

---

## 7. Not Yet a Stable External Protocol

> **This document describes the current implementation boundary. It is not yet a versioned public protocol or SDK contract.**

Before exposing this as a stable API, the following questions must be answered:

- Should agents carry their own cryptographic keys?
- Should endorsements be signed?
- Should the contract include authentication of the agent itself, or only the PreAuthorization document?
- What is the minimal wire format for external agents (e.g., Darwin) to submit proposals?

Until these are resolved, this document is an **internal extraction**, not a public specification.

---

## 8. Future Contract Extraction

The next step is to review whether this extracted contract reveals any problem in Sanad itself:

- Is the lack of signed endorsements a **bug** to fix?
- Is it a **test gap** to document with an adversarial test?
- Is it an **architecture decision** to accept as a known limitation?

Only after this review can a stable external contract be defined.

**See also:** `docs/darwin-threat-surface.md` for the attack-surface analysis that informed this contract.
