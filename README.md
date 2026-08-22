# Sanad (سند) — Execution & Accountability Gateway for AI Agents

> **Sanad** (Arabic: سند) means *a deed; a document that proves*.

AI agents are starting to spend money, book services, and act in the real
world on people's behalf. The hard problem is not whether an agent *can* act —
it's whether anyone can answer, afterwards and under dispute:

1. **Who authorized this action?**
2. **Under which exact terms at that moment?**
3. **What actually happened at the provider?**
4. **Can we prove it — even if the system crashed mid-flight or someone
   tampered with the permissions?**

Sanad is a small, auditable gateway that sits between an agent and the world
and makes those four questions answerable — by design, not by log-grepping.

**This repository ships both halves:** the gateway (`sanad/`), and **Wakeel**
(`wakeel/`) — a working refund agent that has *no code path to money except
through Sanad*. You can watch it approve, escalate, and be stopped.

بالعربية: «سند» بوابة تنفيذ ومحاسبة للوكلاء الأذكياء — كل فعلٍ يمرّ عبرها
يحمل جواباً موثَّقاً: من سمح؟ بأي شروط؟ ماذا حدث فعلاً؟ وما الدليل؟
ومعها «وكيل»: وكيل استرداد يعمل فعلاً، لا يملك طريقاً إلى المال إلا عبر سند.

## The core ideas

**Autonomy is a pre-signed approval, not the absence of approval.**
The human signs a pre-authorization *document* (limits, categories, daily
budget). Signing snapshots the document's raw bytes and hash into the ledger.
Every autonomous action derives its approval from that signed snapshot and
records `derived_from`. One execution path for humans and agents — only the
`approver` field differs.

**An approval is consumed exactly once, atomically.**
A SQLite unique-constraint INSERT is the race arbiter: ten concurrent workers
holding the same approval produce exactly one claim, one provider call, and
nine explicit denials. The claim is durable and written *before* the provider
call — a crash cannot resurrect a burned approval.

**Tampering kills the whole document.**
Change one byte of the pre-authorization after signing and *every* derivation
is denied with `PRE_AUTH_HASH_MISMATCH` — including operations that were legal
under both versions. A signature covers a document, not clauses. Control
returns only through a new explicit signature.

**Settlement comes from reality, not assumption.**
If a provider response is lost, the execution is `UNKNOWN` — never silently
retried. On startup, a recovery loop re-settles every unresolved execution by
asking the provider itself (saved receipt first, metadata search as fallback):
`EXECUTED`, `NOT_EXECUTED` (safe to retry with a *new* approval), or
`UNRESOLVED` (re-enters next run).

**The ledger is append-only proof.**
What happened is never erased; what is unresolved never becomes a new
execution. Budgets are derived from the ledger's own `EXECUTED` lines — no
parallel counter that can drift.


## Wakeel — the agent that cannot go around the brakes

A support agent's most repetitive real task is issuing refunds. `wakeel/` is
that agent: it reads a ticket in plain language, classifies the reason, checks
the refund policy (window, amount, charge status), and hands a *proposal* to
Sanad. It contains no payment code. If Sanad denies, the agent stops — there
is no second route to the provider, and a test asserts the agent exposes no
public method that could become one.

Three outcomes, and only one of them touches money:

| Outcome | Meaning |
|---|---|
| `AUTO` | within the signed pre-authorization → Sanad derives approval and executes |
| `ESCALATE` | outside the signed limits, or a judgement call → a human must approve |
| `REFUSE` | the refund policy itself says no (outside the window, already settled) |

One deliberate asymmetry: if Sanad denies because the **pre-authorization
document was altered**, a human approval cannot override it. Re-signing is the
only way back. Working *around* a broken signature would make signatures
meaningless.

Run against Stripe test mode (`wakeel/exp006_demo.py`): a five-ticket queue
produces automatic refunds for the eligible ones, holds the rest at
`ESCALATE_HUMAN`, and completes one of them only after a named manager
approves it — every step in the ledger.

## Quickstart

```python
from sanad import Ledger, ClaimStore, PreAuthorization, Gateway, recover_on_startup
from sanad.providers.stripe import StripeProvider

ledger  = Ledger("ledger.jsonl")
claims  = ClaimStore("claims.db")
preauth = PreAuthorization(ledger, "pre_auth.json")   # your signed limits
gateway = Gateway(ledger, claims, preauth,
                  StripeProvider(secret_key="sk_test_..."))

recover_on_startup(ledger, claims, gateway.provider)  # heal before acting

preauth.sign(approver="you")                          # explicit human act

approval = gateway.derive_approval("coffee", 1200, "USD")   # 12.00 USD
gateway.execute(approval)                             # claim -> call -> proof
```

`pre_auth.json`:

```json
{
  "auto_limit_minor": 5000,
  "daily_budget_minor": 20000,
  "currency": "USD",
  "blocked_categories": ["investment"]
}
```

Open `viewer/ledger_viewer.html` in a browser and drop `ledger.jsonl` on it to
see the register: stamps, hash seals, and the raw JSON evidence behind every
entry. Everything is read locally — nothing is uploaded anywhere.

## What is proven, and how

Every invariant here graduated from a numbered experiment with a frozen
expected outcome, run against Stripe test mode before being distilled into
this library:

| Invariant | Experiment | Now automated in |
|---|---|---|
| Execution leaves evidence; UNKNOWN is settled from reality | EXP-000/001 | `tests/test_atomicity.py` |
| Authorization is a policy snapshot, not a live reference | EXP-002 | `tests/test_preauth.py` |
| One approval = one atomic claim = one provider call (10-way race, 20 rounds) | EXP-003 | `tests/test_atomicity.py` |
| Signed pre-auth; tamper kills the document; budget from the ledger | EXP-004 | `tests/test_preauth.py` |
| An MCP agent is forced through Sanad; six scenes over the real protocol | EXP-005 | `tests/exp005_mcp_local.py` |
| A working agent has no path to money outside the gateway | EXP-006 | `tests/test_refund_agent.py` |
| A name can be typed; a signature cannot be forged | EXP-005 | `tests/test_identity.py` |

```bash
pip install pytest && pytest tests/ -q     # 27 passed
```

## Scope and honesty

- This is a v0.1 single-node gateway. The atomic claim is SQLite-based:
  correct on one machine, not yet a distributed lock.
- Two providers (Stripe payments, Stripe refunds) are implemented; the
  `Provider` interface is four methods — bookings and other commitments are
  the roadmap.
- The private key lives in a local file; where it should live and how it is rotated is not yet addressed. `trusted_keys` is now the root of trust — it is an ordinary file, not itself signed.
  enough to prove the flow; not yet enough to prove identity.
- Wakeel is a demonstration agent, not a support-desk product: it reads
  tickets it is handed, it does not connect to a helpdesk.
- Time-based pre-auth expiry is deliberately absent until it has its own
  test scene. A signed document carries no dead clauses.

## License

MIT
