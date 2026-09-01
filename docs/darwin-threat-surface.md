# Darwin → Sanad Threat Surface Map

**Date:** 2026-09-01  
**Scope:** Analyze Darwin capabilities as an example of the class of agents Sanad is designed to govern. Determine what Sanad already covers, what is missing, and what is out of scope.

**Principle:** Sanad is an authorization and accountability layer, not a general security operating system. We do not expand Sanad's architecture to chase theoretical threats.

---

## 1. Capability Analysis

| # | Darwin Capability | Sanad Relevance | Classification | Evidence / Rationale |
|---|---|---|---|---|
| 1 | **Email sending** | Communication channel | **Out of Scope** | Sanad validates signed financial intent, not email content. If an email triggers a financial workflow, Sanad sees the *proposal*, not the email. |
| 2 | **File modification** | Tampering with `pre_auth.json` or ledger files | **Test Gap** | Architecture: `TimedSignedPreAuthorization` is cryptographically signed. Tampering breaks the signature → Sanad refuses. **Missing:** explicit adversarial test where an agent modifies the pre-auth file and Sanad rejects it. |
| 3 | **Web browsing / API calls** | Intelligence gathering (quotes, vendor lookup) | **Out of Scope** | Sanad does not govern research or information retrieval. **Becomes Architecture Gap only if** Darwin uses web APIs to execute financial transfers directly, bypassing Sanad. |
| 4 | **Automation / Tool execution** | Running scripts, calling tools | **Architecture Gap** | Sanad currently governs financial execution via `TimedGateway` only. If Darwin has non-financial sensitive tools (delete files, run commands), Sanad does not see them. |
| 5 | **Memory / Context** | "The manager approved this before" | **Covered** | Sanad's authorization decisions are based on signed structural facts, not on agent memory or natural-language claims. Memory influences agent reasoning, not Sanad validation. |
| 6 | **Multi-tool orchestration** | Tool A result → Tool B execution | **Architecture Question / Future ADR** | If Tool A (read email) feeds data into Tool B (transfer money), and only Tool B passes through Sanad, Sanad misses the provenance chain. **Not yet decided** whether tracing arbitrary tool chains belongs in Sanad's core mission. |
| 7 | **Multi-agent delegation** | Agent A delegates to Agent B | **Architecture Gap** | Sanad has no native delegation chain. There is no "parent authority ≥ child authority" enforcement because there is no parent/child relationship in the current architecture. |
| 8 | **Voice input** | Voice command → action | **Future** | Voice is an input modality. If voice → signed proposal → Sanad, Sanad can validate. If voice → direct Darwin execution, Sanad is bypassed. |
| 9 | **API integrations / Credentials** | Holding bank API keys | **Architecture Gap** | If Darwin holds provider credentials directly, it can execute financial actions without passing through Sanad. **Future design principle:** Sanad holds the keys; the Agent holds the intent. |
| 10 | **Autonomous actions** | Self-initiated execution without human prompt | **Partially Covered** | Sanad's ledger records *who* proposed, *who* endorsed, and *what* was executed for financial actions. But if the action is non-financial, Sanad does not record it. |

---

## 2. Classification Summary

| Class | Count | Action |
|---|---:|---|
| **Covered** | 1 | No action needed. Architecture already protects against this threat. |
| **Partially Covered** | 1 | Document boundary; no immediate implementation. |
| **Test Gap** | 1 | Write an explicit adversarial test (EXP) to prove the architecture holds under agent attack. |
| **Architecture Gap** | 3 | Document as future ADRs. Do not implement until they become part of Sanad's core mission. |
| **Architecture Question** | 1 | Decide later whether this belongs in Sanad's core mission. |
| **Out of Scope** | 2 | Explicitly reject. Sanad is not a communication filter, search engine, or general OS. |
| **Future** | 1 | Revisit when multi-modal input becomes a concrete requirement. |

---

## 3. Immediate Action Items

### Test Gap → EXP
- **EXP-XXX:** Agent attempts to tamper with `pre_auth.json` on disk. Sanad must refuse because signature verification fails.

### Architecture Gaps → Backlog ADRs
- **ADR-XXX:** Tool Governance Model — Should Sanad gatekeep *all* sensitive tools, or only financial execution?
- **ADR-XXX:** Credential Isolation — Should Sanad be the exclusive holder of provider API credentials?
- **ADR-XXX:** Native Agent Delegation — If Sanad introduces per-agent mandates, how is "child ≤ parent" enforced?

### Architecture Question → Future Decision
- **ADR-XXX:** Cross-Tool Provenance — Should Sanad trace intent across arbitrary tool chains, or is this outside its core mission?

---

## 4. Out of Scope (Explicitly Rejected)

The following are **not** Sanad's responsibility and should not drive architecture changes:

- Email content inspection
- Web browsing control
- Agent memory curation
- General operating system security

**Rationale:** Sanad's invariant is `NO AGENT EXECUTES` for *sensitive actions* without authorization. It is not `NO AGENT COMMUNICATES` or `NO AGENT THINKS`.

---

## 5. Key Design Principle Extracted

> **Sanad holds the keys; the Agent holds the intent.**

This is a **future design principle**, not a description of the current architecture. It guides future credential-isolation decisions without requiring immediate implementation.

---

## 6. Next Step

From this Threat Map, extract the **minimal Agent Contract** required for any agent (Darwin or otherwise) to be governable by Sanad. The contract must be derived from what Sanad *actually validates today*, not from an idealized protocol.

See: `docs/agent-contract.md` (to be written after this map is reviewed).
