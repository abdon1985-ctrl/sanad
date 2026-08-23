# Sanad — An Adversarial Report

**Sanad doesn't authorize agents to pay. It makes agent authorization
accountable after the authorization exists: who acted, under which
mandate, and what happened when the provider went silent.**

This is not a launch post. It is a report on a system that was frozen
before testing, attacked, and changed by what the attacks found. It
lists the failures first, the fixes second, and ends with what remains
unproven. If you can break a claim below, that is a contribution, not
an embarrassment — see *How to attack this* at the end.

Repository: `github.com/abdon1985-ctrl/sanad` — ~1,000 lines of
library code, 84 tests across 13 experiments (EXP-000..012), Python,
no framework.

---

## Method

Every experiment follows the same discipline:

1. **Expectations are frozen before the run** — written into the test
   file as assertions, including assertions that a defence *fails*.
2. **A gap found is a gap named**, in the docstring of the code that
   has it, not in a private note.
3. **Output text is never evidence.** Twice during development a
   printed "pushed ✓" was false and the git history caught it. The
   repository is the source of truth; the tests are the claims.
4. Several tests are *negative controls*: the checking code was
   deliberately mutated to confirm the tests actually depend on it.

## The system, briefly

- `ledger.py` — append-only JSONL audit trail; daily spend is derived
  from EXECUTED rows, never a parallel counter.
- `claims.py` — SQLite atomic claim: an approval is consumed exactly
  once, under real thread races.
- `identity.py` / `validity.py` — Ed25519 signing; the mandate is a
  signed document with TTL and revocation, verified against the bytes
  on disk at every use.
- `gateway_timed.py` — the full authorization check runs before term
  checks; expired approvals are refused *before* the claim.
- `reconcile.py` — a provider timeout yields `UNKNOWN`, never
  retry-and-hope. Settlement asks the provider what actually
  happened: `EXECUTED` / `NOT_EXECUTED` / `UNRESOLVED` (re-enters).
- `binding.py` (EXP-009) — approvals are bound to an agent key;
  the mandate is pinned to a named document.
- `chain.py` (EXP-010) — hash-chained ledger with signed checkpoints.
- `ap2_adapter.py` (EXP-011) — Sanad as an accountability layer over
  AP2 mandates (real structures from `google-agentic-commerce/AP2`).
- EXP-012 — daily spend derives from executions *and* settlements, so
  money recovered from silence is money the budget can see.
- `demos/live_three_agents.py` — the full path run against real
  Stripe (test mode), including a genuinely lost response settled by
  asking Stripe.

## Findings — the failures we found, frozen as tests

### F1. An approval was a bearer token (EXP-008, test A1)
Three agents shared one signed mandate. Agent `topup` executed an
approval derived by agent `buyer`. **The theft succeeded** — the
approval carried an amount and a currency and no holder — and the
ledger could not even name the thief: no execution row recorded which
agent acted. Frozen as a passing test asserting `EXECUTED`.

**Fixed in EXP-009:** approvals are bound at derivation to the
agent's public key; execution requires a fresh signature over the
`approval_id`. The stolen dict is useless (the binding lives in the
ledger, not the object), a name-only impostor fails, and — the detail
that matters — refusal happens *before* the atomic claim, so a
rejected theft does not burn the victim's approval.

### F2. Refusal is not containment (EXP-008, test A2)
A rogue signed the mandate document with an untrusted key. Sanad
refused it — correctly. But "current mandate" was defined as *the
last signed row*, so the rogue's row displaced the real mandate and
**froze the two honest agents along with the attacker**: a
denial-of-mandate at the cost of one ledger line.

**Fixed in EXP-009:** the mandate is pinned to an explicit
`pre_auth_id`. Later signed rows remain visible in the ledger but do
not become the mandate. The stated price (frozen as test B7): a *new
legitimate* signature does not move the mandate either — moving is an
explicit act.

### F3. The budget could not see settled spend (live Stripe demo)
In the live run, ledger-derived spend read 7,000 minor units while
Stripe had actually been charged 8,000. An execution that ended
`UNKNOWN` and was later settled `EXECUTED` by reconciliation was
recorded as a `resolve` row — and `spent_today_minor()` read only
`execute/EXECUTED` rows. **Money left; the budget didn't see it.**
Found because the live demo combined chaos and budget in one path,
which no isolated experiment had done.

**Fixed in EXP-012:** `resolve` rows now carry `amount_minor`, and
daily spend derives from `execute/EXECUTED` + `resolve/EXECUTED`. No
existing row is touched — the ledger stays append-only. Nothing is
counted twice: reconciliation writes the amount only when no
`execute/EXECUTED` row already counted that execution (the case where
the receipt was saved and only the claim status died). One semantic
decision is stated rather than hidden: settled spend counts on the day
it became KNOWN, not the day it happened — the ledger refuses to
backdate what it only just learned.

### F4. The chain proves consistency, never completeness (EXP-010,
tests C5/C6 — limits frozen on purpose)
EXP-010 hash-chains the ledger. Editing, deleting, or reordering a
row is caught and located. Two attacks are **deliberately asserted as
NOT caught**:

- *Truncation*: a shorter honest prefix is a perfectly valid chain.
- *Full rewrite*: whoever holds the file recomputes every hash in
  milliseconds. Local hashes cannot bind a local attacker.

Both are answered only by a root published somewhere the file's
holder does not control. A first signed root is published in
`CHECKPOINT.json` in this repository; `verify_against_published()`
is the only function in `chain.py` that survives an attacker holding
the file, and its docstring says so.

### F5. Sanad anchors AP2 credentials; it does not verify them
(EXP-011, test P8 — boundary frozen on purpose)
The AP2 adapter accepts a garbage `user_authorization` string and
anchors it successfully. Verifying the JWT/VC signatures is the
credential provider's and the network's job; re-implementing it here
would be verification theater. What Sanad fixes into the chained
ledger is the *hash of exactly what was presented*, plus the two
things AP2 itself does not record: which agent acted, and what
happened when the provider went silent. Anyone describing the adapter
as "verifying AP2" should be pointed at test P8.

## What remains unproven

- **Key custody.** Where the operator's private key lives, how it
  rotates, and who issues agent keys — three declared gaps, one
  question. Everything cryptographic above rests on it. The published
  checkpoint was sealed with an ephemeral key: it proves the root,
  not a durable identity.
- **`trusted_keys` bootstrap.** The root of trust is itself
  configuration; its own integrity is asserted, not proven.
- **Distribution.** Atomicity (one approval, one execution) is proven
  under threads, not across machines. The ledger is a local file, the
  claim store a local SQLite. Real agent fleets are distributed; this
  is a redesign, not a port.
- **Independence.** Every test here was written by the same party
  that wrote the code. Prosecutor and judge are one. That is the
  largest open weakness of this report, and the reason it exists.

## How to verify

```
git clone https://github.com/abdon1985-ctrl/sanad
cd sanad && pip install pynacl pytest
PYTHONPATH=. python -m pytest tests/ -q        # expect: 84 passed
```

Chain and published root:

```python
from sanad.chain import ChainedLedger, verify_chain, verify_against_published
import json
lg = ChainedLedger("CHECKPOINT_LEDGER.jsonl")
cp = json.load(open("CHECKPOINT.json"))
print(verify_chain(lg))
print(verify_against_published(lg, cp["height"], cp["root"]))
```

## How to attack this

Open an issue titled `external finding: <claim you broke>`. State the
claim as this report words it, and how you broke it. The response
will not be a defence; it will be a frozen test reproducing your
attack, and either a fix or a named gap. That exchange — not the 84
green tests — is what this project considers evidence.
