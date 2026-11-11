# EXP-016 / EXP-017 — review record (single file)

**Status: documentation of a candidate fix package. NOT merged. NOT independently verified.**
Baseline: uploaded snapshot of the repo (git `d4e7941` was never verified; the source files were later confirmed byte-identical to the snapshot the patches were built on).
Tests were run with a stand-in runner (`mini_pytest`), not real pytest. Real pytest and an independent review are still owed.

Companion files (same folder): `review_exp016.patch` (sha256 `37f825e9a78a274e5489134105041b2172909cfb3f937a49217dbdd2ab0d60e8`), then `review_exp017.patch` (sha256 `8d9192a01e5ecce7ebcc4080f74e18bddacba45d447e69aea1aad5653c2f3da1`). They are records only: do not apply them to a branch until reviewed. Apply order: 016 then 017.

Contents: 1 Decisions · 2 EXP-016 report · 3 EXP-017 report · 4 F-G usage report · 5 Findings register · 6 Execution-path audit · 7 Evidence tests

---
# 1. Decisions

Nothing here is merged, pushed, independently verified, or final.

| # | Decision | Status |
|---|---|---|
| D-1 | Budget is reserved atomically at grant (`Consumed + Reserved ≤ Budget`) in `claims.db` via `BEGIN IMMEDIATE` | accepted technically (author tests) |
| D-2 | Human approval may exceed the daily budget, but its full spend is recorded and counts against later gated checks; the agent's budget stays mandatory (`gated=False`, counted) | approved |
| D-3 | Budget day = reservation day; `spent_today_minor()` (F3: counted on the day it became known) unchanged; execution/settlement days stay in ledger rows | approved |
| D-4 | ORPHAN release only if RESERVED, no GRANTED row, past grace, and no send evidence anywhere; otherwise `reserve/ANOMALY`, budget stays held | approved |
| D-5 | `UNKNOWN` stays held (`SENT`) until settlement; expiry never releases SENT/CONSUMED | approved |
| D-6 | Expiry is decided from the trusted reservation in `begin_send`: **expired ⇔ now > expires_at**, same boundary in execute, sweeper and recovery | approved |
| D-7 | `ApprovalExpiry = min(TTLExpiry, MandateExpiry)`; the approval never outlives the mandate's `valid_days` (`test_T3` edited accordingly) | approved |
| D-8 | An already-sent approval re-executed answers `DENIED_APPROVAL_CONSUMED`, not `EXPIRED` | approved |
| D-9 | Mandatory TTL deferred (EXP-017b); human approval without TTL does not expire | approved |

Still outstanding: real pytest run · independent code review · decision on findings F-A…F-K · merge/push approval.
Baseline caveat: patches were built and tested on the uploaded zip snapshot; git `d4e7941` was never verified.

---
# 2. EXP-016 report

**Status: FIX IMPLEMENTED on a separate copy — NOT merged, NOT pushed, NOT approved.**
GAP-BUDGET-1 was OBSERVED (rf009-gap-evidence). This report covers its fix only.
GAP-TTL-1 (EXP-017) is **not fixed** and is verified still open on the patched tree.

## Baseline
The upload has no `.git`; requested base `d4e7941` is **unverifiable**. Baseline = the uploaded zip, committed locally as `cd7cbe8` only to produce the patch. `exp016.patch` applies cleanly to a pristine extract of the zip (`git apply --check`).

## Method (tests first)
1. `tests/test_exp016_reservation.py` (R1–R20) written before any production change and run on the baseline.
2. Fix. 3. Re-run new tests, full suite (x3), original gap tests, and a negative control.

## Results
| Run | Result |
|---|---|
| New tests on **baseline** | 4 pass (controls R1, R6, R13, R14), **9 FAIL behaviourally**, 7 ERROR (new API absent — proves nothing about behaviour) |
| New tests on **patched** | **20 / 20 pass** |
| Full existing suite, baseline | 95 pass, 1 fail, 1 collect error |
| Full existing suite, patched (3 runs, identical) | 115 pass (= 95 + 20 new), same 1 fail, same 1 collect error |
| Negative control (budget check disabled in scratch copy) | 14 of 20 fail → the tests depend on the fix |
| Original GAP-BUDGET-1 tests on patched | B1 pass; **B2, B3 flip to FAIL** (= gap closed; expected flip) |
| Original GAP-TTL-1 tests on patched | T1–T4 pass → **TTL gap still OPEN** (needs a ClaimStore-compatible copy; original file's duck-typed fake predates the new API) |

Pre-existing, unrelated, unchanged: `test_W7` fails on baseline too (3×4000 = 12000 does not exceed the 12000 budget, so its `len(denied) >= 1` can never hold; W8 is the real test). `test_api.py` cannot be collected (fastapi not installed).

## Design as implemented
- `reservations` table in `claims.db`. `reserve()` = check-and-hold inside `BEGIN IMMEDIATE` (serialises threads **and processes**).
- States: `RESERVED → SENT → CONSUMED | RELEASED`; `RESERVED → RELEASED` (EXPIRED / ORPHAN). Counted: RESERVED, SENT, CONSUMED. `UNKNOWN` stays SENT = held.
- `begin_send()`: claim row + RESERVED→SENT in one transaction; refusal consumes nothing. No live reservation ⇒ `DENIED_NO_RESERVATION`, zero provider calls.
- `set_status()` moves claim and reservation in one transaction; CONSUMED/RELEASED are terminal, so a repeated status can never return budget twice.
- Write order: SQLite reserve → ledger GRANTED. Crash between ⇒ ORPHAN.
- ORPHAN rule (as agreed): released only if RESERVED, no GRANTED row, past a grace window, and **no send evidence** in table or ledger. SENT/UNKNOWN never released for a missing ledger row. Unexplained ⇒ `reserve/ANOMALY`, budget stays held (fail closed).
- `expires_at = min(now+approval_ttl, signed_at+valid_days)` from **signed terms**, stored in the table; used only to return an unexecuted reservation's budget. Execute-time TTL enforcement still reads the caller's dict (EXP-017).
- `verify_reservations()` compares SQLite and ledger, report-only, assumes neither is complete.
- Budget day = **reservation day**. `Ledger.spent_today_minor()` (F3: counted on the day it became known) is untouched; D1–D6 pass. Execution day and settlement day remain visible in the ledger rows.

## Deviations from the plan I proposed — please review
1. **Human approvals:** I proposed `counts_toward_budget=0`. Implemented instead as `gated=False`: not budget-gated at grant (unchanged behaviour) but **counted as spend** for every later gated check. Safer: the flag cannot be used to hide spend. Test R13.
2. `recover_on_startup` gained optional `now` and `orphan_grace_seconds` (default 60) and now also runs `recover_reservations`. Return value unchanged.
3. Declared edits to existing tests: `FakeClaims` in `test_gateway_timed.py` and `test_unknown_lab.py` gained `reserve`, `release_expired`, `begin_send` (old semantics preserved). No assertion was changed.

## Known limits / consequences (not fixed here)
- **TTL interplay:** an expired RESERVED row is released lazily (at the next derive or recovery). Once released, a tampered-dict execute is refused; before that, the EXP-017 bypass still works. Enforcement is therefore timing-dependent until EXP-017 enforces `expires_at` in `begin_send`.
- **Upgrade:** approvals GRANTED before this patch have no reservation and are denied at execute (`DENIED_NO_RESERVATION`); pre-patch ledger spend is not in the table (matters only on the upgrade day).
- A budget-day boundary: an approval reserved at 23:59 and executed at 00:01 counts against the earlier day (by design).
- `try_claim()` is retained for compatibility but bypasses reservations; the Gateway no longer uses it.
- A caller that derives approvals and never executes them holds budget until expiry (or midnight if no TTL): denial-of-budget; real fix is EXP-017's trusted expiry plus a TTL requirement.
- Out of scope, observed, unfixed: `grant_human_approval` is unauthenticated; `execute` does not re-verify the mandate/key; no input validation (negative/non-int amounts, case-variant categories); single-node SQLite.
- Real process death was tested once (`os._exit`, R20); other crash points are simulated by exceptions/BaseException.
- R3 uses a barrier (forced interleaving); R4 uses real forked processes (Linux only).

## Evidence integrity
- Runner: `tools/mini_pytest.py` is a **minimal stand-in, NOT pytest** (pytest not installed, no network). Tests are plain pytest-style; a real pytest run is still owed.
- No Stripe; providers are in-memory/file witnesses.
- Hashes: `SHA256SUMS.txt`.

---
# 3. EXP-017 report

**Status: FIX IMPLEMENTED on a separate copy, stacked on EXP-016 — NOT merged, NOT pushed, NOT independently verified.**
Evidence class: author tests, `mini_pytest` runner (NOT real pytest).

## Stack and baseline
- Base = EXP-016 patched tree. `exp016.patch` (sha256 `37f825e9…`) is **unchanged**; its folder's SHA256SUMS still verify 15/15.
- Upstream base is still the uploaded zip (git `d4e7941` unverifiable). Reproduced from the pristine zip: `git apply exp016.patch` then `git apply exp017.patch` → both apply, full suite 131 pass / 1 fail / 1 collect error (`logs/stack_reproduction_full_suite.txt`).

## Fix
Expiry is decided by `ClaimStore.begin_send(approval_id, execution_id, now)` **inside the transaction that claims the approval**, from `reservations.expires_at` (written at derivation from SIGNED terms: `min(issue + approval_ttl_seconds, signed_at + valid_days·86400)`; human approvals: `now + ttl_seconds`). Rule everywhere: **expired ⇔ now > expires_at** (valid at equality).
- `begin_send` returns `OK | CONSUMED | EXPIRED | EXPIRED_ALREADY | NO_RESERVATION | RESERVATION_<S>`; `EXPIRED` also releases the reservation atomically; only the call that released it logs the release.
- `TimedGateway.execute` no longer reads `ttl_seconds`/`issued_at` (kept in the dict for information only). BoundGateway inherits the rule (X9).
- Ledger: `execute/DENIED_APPROVAL_EXPIRED` then `reserve/RELEASED(EXPIRED)`. A crash between them (or before either) is repaired idempotently by `recover_on_startup`; tested at both write points, recovery run twice (X13).
- Sweeper (`_sweep_expired`, `release_expired`) and recovery moved from `<=` to `<` to match.
- `verify_reservations` adds `EXPIRY_MISMATCH` (table vs GRANTED row).
- SENT/UNKNOWN/CONSUMED are never released by expiry; a re-executed already-sent approval reports `DENIED_APPROVAL_CONSUMED`.

## Results
| Run | Result |
|---|---|
| New tests (X1–X16) on **base (EXP-016 tree)** | 2 pass (controls X1, X4), **12 FAIL behaviourally**, 2 ERROR (new `begin_send(now=)` signature) — the third control X13 also passes there |
| New tests on **patched** | **16 / 16** |
| Full suite base | 115 pass, 1 fail, 1 collect error |
| Full suite patched (3 identical runs) | **131 pass** (=115+16), same 1 fail (`W7`, pre-existing), same collect error (fastapi) |
| Original GAP-TTL-1 tests | T2, T3 **flip to FAIL (gap closed)**; T1, T4 pass (ClaimStore-compatible v2 copy) |
| Original GAP-BUDGET-1 tests | B2, B3 still flipped (EXP-016 intact); B1 passes |
| Negative controls (4 mutants, all caught) | M1 expiry check removed → 10 fail; M2 `>=` in begin_send → 4 fail (boundary); M3 sweeper `<=` → 2 fail (X5, X6); M4 old dict check restored → 5 fail (single source of truth) |
Note: X13 passes on the base because EXP-016's recovery already releases expired rows; it is a regression guard there, not a gap test.

## Decisions I took that change behaviour — please accept or reject explicitly
1. **An approval no longer outlives its mandate.** `valid_days` caps the approval (agreed `min(...)`). This contradicted the existing `test_T3_no_ttl_never_expires` (30 days later, `valid_days` 7). I edited that test to also drop `valid_days`, preserving its real intent (no approval ttl ⇒ no approval-level expiry) and pointing to X7. **This is a declared semantic change.**
2. Already-sent approvals re-executed after expiry now answer `DENIED_APPROVAL_CONSUMED` (truthful) instead of `EXPIRED`.
3. Expired approvals are terminal `RELEASED`; they can never be reused.
4. Declared edits to existing tests: `FakeClaims` in `test_gateway_timed.py` / `test_unknown_lab.py` now honour `expires_at` in `begin_send(now=)`; `test_T3` as in (1). No other assertion changed.

## Limits — not fixed, not claimed
- **Clock and storage are trusted.** `expires_at` lives in `claims.db`; whoever can write that file can alter it. `verify_reservations` detects a mismatch with the ledger only while the ledger is intact, and the same actor may be able to write both. The gateway clock is the process's own.
- The denial row can be lost if the process dies before writing it; the release is always repaired, the refused attempt's record is best-effort.
- Plain `Gateway` has no approval lifetime (expires None). Human approval without TTL never expires (agreed). No mandatory TTL → budget can still be held until expiry/day end by approvals never executed (EXP-017b, separate).
- Not re-verified at execute: mandate revocation/key standing after derivation; unauthenticated `grant_human_approval`; input validation (negative/non-int amounts, category evasion). Single-node SQLite.
- Real pytest not run; no Stripe; X16 uses threads at claims level; the one real-process-death test is EXP-016's R20.
- Pre-existing and unrelated: `test_W7` fails on base; `test_api.py` needs fastapi.

## Files
`exp017.patch` (stacked on exp016.patch), `exp017-patched-tree.zip`, `tests/test_exp017_expiry.py`, `tools/` (runner, TTL-gap compat copy), `logs/`, `SHA256SUMS.txt`.

---
# 4. F-G usage report

Status: **GAP OBSERVED, not fixed.** No production or test code was changed for this report.

## 1. The gap (demonstrated, `test_gap_fg_try_claim.py`, 4/4 as expected)
`try_claim()` inserts the claim row but does not move the reservation. Result:
- G2: the approval is burned (`DENIED_APPROVAL_CONSUMED`, zero provider calls) while its reservation stays `RESERVED` with `execution_id = NULL`.
- G3: startup recovery settles the orphan claim `NOT_EXECUTED` but cannot release the reservation (it joins on `execution_id`, which is NULL). On the plain `Gateway` (no expiry) the budget is then held until the day ends, with nothing ever spent.
- G4: `verify_reservations` does not report it.
- G1 (control): the supported path, `begin_send` then recovery, releases correctly.
Impact: budget held without spend (a denial-of-budget), not an overspend.

## 2. Every reference in the repository
| Where | What | Real caller? |
|---|---|---|
| `sanad/claims.py:33` | the definition | — |
| production code (`sanad/`, `sanad_mcp/`, `wakeel/`, `api.py`) | none; `Gateway.execute` uses `begin_send` since EXP-016 | **no** |
| `tests/test_three_agents.py:240` | `claims.try_claim(approval_id, "EX-probe") is True`, a probe that a refused attempt did not consume the approval | **yes, the only call** |
| `tests/test_workflow.py:293` | name of `test_try_claim_prevents_double_execution`; it calls `gw.execute` twice, never `try_claim` | no (name only) |
| `tests/test_gateway_timed.py:45`, `tests/test_unknown_lab.py:48` | `FakeClaims.try_claim`; no longer called by `Gateway` | dead fake methods |
| `REPORT.md:116,133`; `docs/master-architecture.md:139`; `docs/provider-execution-semantics.md:5,39-42,84,92,95` | describe `try_claim` as THE atomic guard and cite the test by name | **stale** since EXP-016 |
`try_claim` is not in `__all__`, but `ClaimStore` is exported, so the method is reachable by any importer. Outside this repository I cannot know of other users.

## 3. Options
| Option | Breaks | Closes F-G? | Size |
|---|---|---|---|
| A. Delete `try_claim` | `test_three_agents.py:240` (rewrite the probe to read `reservation_rows()`); any outside caller | yes | small, but API removal |
| B. Rename `_try_claim` | same test; outside callers | partly (still callable) | small |
| **C. Keep it as a deprecated wrapper: `try_claim` = `begin_send(...) == "OK"`, emitting `DeprecationWarning`** | **nothing in-repo**: the probe still gets `True` on an unconsumed approval | **yes, at the root: a claim can no longer exist without its reservation moving to SENT, and a burned claim is recoverable (recovery settles it NOT_EXECUTED and releases)** | ~6 lines + docs |
Semantic differences of C, stated: returns `False` also when the approval has no live reservation or is expired (previously only when already claimed); it consumes only if the reservation is `RESERVED`.

## 4. Recommendation (author, advisory to the owner's decision)
Option C as its own small experiment (EXP-018): flip G2–G4 (expected), keep G1, add one test that the probe in `test_three_agents.py` still passes unchanged, update the four stale doc passages, remove the two dead fake methods in a declared edit. Remove the wrapper in a later release once no outside user is confirmed.
Out of scope here: anyone who can call `begin_send` directly can still burn an approval; that is the trusted-component limit already in F-D.

---
# 5. Findings register

Status vocabulary: FIXED-ON-PATCH (author tests) · OPEN · NOTED. Nothing here is independently verified; nothing is merged or pushed.

| ID | Finding | Status | Where |
|---|---|---|---|
| GAP-BUDGET-1 | Granted-but-unexecuted approvals invisible to the daily budget | FIXED-ON-PATCH | EXP-016 |
| GAP-TTL-1 | Expiry decided from caller-editable `ttl_seconds`/`issued_at` | FIXED-ON-PATCH | EXP-017 |
| F-A | `grant_human_approval(approver: str)` is unauthenticated: a name is typed, not signed | OPEN | gateway.py |
| F-B | Mandate/key standing (revocation) is not re-checked at execute; only at derivation | OPEN | gateway.py |
| F-C | No input validation: `amount_minor` sign/type unchecked; `blocked_categories` is a case-sensitive exact match on an agent-supplied string | OPEN | gateway.py / policy |
| F-D | `claims.db`, ledger and the gateway clock are trusted. `expires_at` is rewritable by whoever can write `claims.db`; `verify_reservations` detects mismatch only while the ledger is intact | OPEN (design limit) | claims.py |
| F-E | The `DENIED_APPROVAL_EXPIRED` record can be lost if the process dies first; the release itself is repaired by recovery | NOTED | EXP-017 |
| F-F | No mandatory TTL: unexecuted approvals can hold budget until expiry or day end (EXP-017b candidate) | OPEN | policy |
| F-G | `ClaimStore.try_claim()` is still public and bypasses reservations: it can burn an approval's claim with no provider call, leaving its reservation RESERVED (budget held until expiry). Not an overspend; found in this audit | OPEN (new) | claims.py |
| F-H | An agent that holds the gateway can reach the provider directly (wakeel wraps `gw.provider.execute`). "No code path to money" is convention, not a boundary | OPEN (design limit) | wakeel / gateway |
| F-I | Upgrade: approvals granted before the patch have no reservation and are denied at execute; pre-patch ledger spend is not in the reservation table (matters on the upgrade day) | NOTED — needs an operational notice | EXP-016 |
| F-J | Multi-process safety is tested for SQLite claims/reservations (R4) but NOT for the ledger: `ChainedLedger` was not exercised with concurrent processes | OPEN (untested) | chain.py |
| F-K | `NOT_EXECUTED` after a lagging Stripe search could allow a retry. Stripe calls use an Idempotency-Key (stripe.py:34), but a retry under a NEW approval has a new execution_id, so the key does not protect it | OPEN | reconcile / provider |
| F-L | Pre-existing, unrelated: `test_W7` cannot pass (3×4000 does not exceed 12000); `test_api.py` needs fastapi | NOTED | tests |

## Execution-path audit (author-run; evidence: execution_path_audit.txt)
- The only production call site of `provider.execute` is `gateway.py` (inside `Gateway.execute`). It is preceded, in this order, by the payload checks (`DENIED_NO_SNAPSHOT`, `DENIED_PAYLOAD_MISMATCH`) and `claims.begin_send`; every early-return path precedes the provider call.
- `begin_send` has a single production caller; `reserve` has two (`derive_approval`, `grant_human_approval`). No SQLite access exists outside `claims.py`.
- `TimedGateway.execute` and `BoundGateway.execute` both end in `super().execute`; no override reaches the provider.
- Limits of this audit: static grep over `sanad/`, `sanad_mcp/`, `wakeel/`; not a proof of absence (dynamic dispatch, monkey-patching as in F-H, other repos); not independent.

## Still required before any merge
1. Real pytest run (the runner used was `mini_pytest`, not pytest).
2. Independent review of the stacked patches' actual code, tests and negative controls.
3. Explicit owner acceptance of the EXP-017 semantic change (approval capped by mandate `valid_days`, `test_T3` edit).
4. Decision on F-A…F-K: which become experiments.

---
# 6. Execution-path audit (raw output)

```
# Execution-path audit of the stacked tree (EXP-016 + EXP-017), author-run, 2026-11-09T06:10:34Z
## provider.execute call sites
sanad/gateway.py:229:            result = self.provider.execute(approval["amount_minor"],
wakeel/wakeel_agent.py:85:        orig = gw.provider.execute
wakeel/wakeel_agent.py:87:        gw.provider.execute = lambda a, c, e: orig(a, c, e, charge_id=charge)
wakeel/wakeel_agent.py:91:            gw.provider.execute = orig
## begin_send / reserve / try_claim call sites
sanad/gateway_timed.py:92:    # Gateway.execute -> ClaimStore.begin_send, from reservations.expires_at
sanad/gateway.py:99:        ok, committed = self.claims.reserve(
sanad/gateway.py:133:        self.claims.reserve(approval_id, amount_minor, day, 0,
sanad/gateway.py:186:        res = self.claims.begin_send(approval["approval_id"], execution_id,
## sqlite or claims-table writes outside claims.py
(none)
## Gateway.execute order (line: construct)
163:                 "execute", "DENIED_NO_SNAPSHOT",
175:                 "execute", "DENIED_PAYLOAD_MISMATCH",
186:         res = self.claims.begin_send(approval["approval_id"], execution_id,
229:             result = self.provider.execute(approval["amount_minor"],
231:             self.claims.set_status(execution_id, "EXECUTED")
238:             self.claims.set_status(execution_id, "REJECTED")
## Stripe provider idempotency
34:            req.add_header("Idempotency-Key", idem)
```

---
# 7. Evidence tests (original GAP-OBSERVED tests and F-G)

## test_gap_budget_race.py

```python
# -*- coding: utf-8 -*-
"""GAP-BUDGET-1 — pending approvals are invisible to the daily budget.

STATUS: GAP OBSERVED (not fixed). These tests PASS while the gap EXISTS.
They are frozen expectations in the project's own style (cf. EXP-010 C5/C6):
the assertions describe the CURRENT, flawed behaviour. When the gap is
fixed, B2/B3 are expected to FAIL and must be flipped deliberately —
a fix is a separate act with its own evidence.

Claim under test (REPORT.md / gateway.py docstring): the daily budget is
derived from what actually happened and cannot be exceeded.
Mechanism under test: derive_approval() checks ledger.spent_today_minor(),
which sums only execute/EXECUTED and resolve/EXECUTED rows. A GRANTED
approval that has not executed yet contributes 0.

Measurement rule: FINAL SPEND is read from the PROVIDER's own record of
money moved (independent of the ledger), not from derivation success.

  B1 control : derive+execute one at a time -> budget IS enforced
               (the budget check works; the gap is specifically pending approvals)
  B2 gap     : derive all, then execute all -> provider total > budget
  B3 gap     : N threads, barrier between derive and execute -> provider total > budget

No production code is modified. Fakes implement only the Provider interface.
"""
import json
import os
import tempfile
import threading

from sanad import Ledger, ClaimStore, Gateway, PreAuthorization
from sanad.providers import Provider

BUDGET = 10_000
LIMIT = 5_000
AMOUNT = 5_000          # == auto limit, so each approval is individually legal
ATTEMPTS = 6            # 6 * 5000 = 30000 against a 10000 budget


class MoneyProvider(Provider):
    """Independent witness: counts money that actually 'left'."""
    name = "memory"

    def __init__(self):
        self.calls = 0
        self.total_minor = 0
        self.records = {}
        self._lock = threading.Lock()

    def execute(self, amount_minor, currency, execution_id):
        with self._lock:
            self.calls += 1
            self.total_minor += amount_minor
        rec = {"receipt": "rcpt_" + execution_id, "amount_minor": amount_minor}
        self.records[execution_id] = rec
        return rec

    def find_by_execution_id(self, execution_id):
        return self.records.get(execution_id)

    def retrieve(self, receipt):
        raise NotImplementedError


def build(tmp):
    ledger = Ledger(os.path.join(tmp, "ledger.jsonl"))
    claims = ClaimStore(os.path.join(tmp, "claims.db"))
    doc = os.path.join(tmp, "pre_auth.json")
    with open(doc, "w", encoding="utf-8") as f:
        json.dump({"auto_limit_minor": LIMIT, "daily_budget_minor": BUDGET,
                   "currency": "USD", "blocked_categories": []}, f)
    pre = PreAuthorization(ledger, doc)
    pre.sign("tester")
    provider = MoneyProvider()
    return ledger, Gateway(ledger, claims, pre, provider), provider


def test_B1_control_sequential_flow_enforces_budget():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, gw, provider = build(tmp)
        granted = 0
        for _ in range(ATTEMPTS):
            ap = gw.derive_approval("coffee", AMOUNT, "USD")
            if ap is not None:
                granted += 1
                gw.execute(ap)
        assert granted == BUDGET // AMOUNT == 2
        assert provider.total_minor == BUDGET
        assert ledger.last(stage="approval", state="DENIED_DAILY_BUDGET") is not None


def test_B2_GAP_derive_all_then_execute_all_exceeds_budget():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, gw, provider = build(tmp)
        approvals = [gw.derive_approval("coffee", AMOUNT, "USD")
                     for _ in range(ATTEMPTS)]
        granted = [a for a in approvals if a is not None]
        # Nothing executed yet -> spent is 0 -> every derivation passes.
        assert len(granted) == ATTEMPTS
        assert ledger.spent_today_minor() == 0
        results = [gw.execute(a) for a in granted]
        assert all(r["state"] == "EXECUTED" for r in results)
        # FINAL SPEND (provider-side) exceeds the signed daily budget.
        assert provider.total_minor == ATTEMPTS * AMOUNT == 30_000
        assert provider.total_minor > BUDGET
        # The ledger agrees with the provider: this is not a bookkeeping artefact.
        assert ledger.spent_today_minor() == provider.total_minor


def test_B3_GAP_concurrent_derivations_exceed_budget():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, gw, provider = build(tmp)
        barrier = threading.Barrier(ATTEMPTS)   # forces: all derive, then all execute
        outcomes = [None] * ATTEMPTS

        def worker(i):
            ap = gw.derive_approval("coffee", AMOUNT, "USD")
            barrier.wait(timeout=10)
            outcomes[i] = gw.execute(ap) if ap is not None else None

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(ATTEMPTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        executed = [o for o in outcomes if o and o["state"] == "EXECUTED"]
        assert len(executed) == ATTEMPTS
        assert provider.calls == ATTEMPTS
        assert provider.total_minor == 30_000 > BUDGET


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items()
                           if n.startswith("test_")):
        try:
            fn()
            print("PASS", name)
        except Exception as e:  # noqa
            failed += 1
            print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)

```

## test_gap_ttl_bypass.py

```python
# -*- coding: utf-8 -*-
"""GAP-TTL-1 — approval expiry is enforced on caller-supplied fields.

STATUS: GAP OBSERVED (not fixed). These tests PASS while the gap EXISTS;
when it is fixed, T2/T3 are expected to FAIL and must be flipped
deliberately, as a separate act.

Claim under test: "expired approvals are refused BEFORE the claim, so they
are never consumed and never reach a provider" (gateway_timed.py).
Mechanism under test: TimedGateway.execute() reads ttl_seconds and
issued_at from the approval dict passed at call time. The EXP-014 fix binds
item / amount_minor / currency to the GRANTED ledger snapshot, but NOT these
two fields, and the ledger GRANTED row does not record them at all.

What each test must show (not merely "a strange value was accepted"):
the provider was actually called and a receipt was recorded AFTER the
signed TTL had elapsed, measured against the TTL in the SIGNED terms
(read back from the ledger), not against the tampered dict.

  T1 control : untampered approval past TTL -> DENIED_APPROVAL_EXPIRED, 0 calls
  T2 gap     : ttl_seconds=None on the dict    -> EXECUTED after expiry
  T3 gap     : issued_at reset to 'now' on dict -> EXECUTED after expiry
  T4 scope   : amount tampering is still blocked (EXP-014 holds) — shows the
               gap is specific to the unbound fields, not a general failure

Scope: TimedGateway only. BoundGateway inherits this execute() via super()
but is NOT exercised here.
No production code is modified.
"""
import json
import os
import tempfile

from sanad.ledger import Ledger
from sanad.identity import Signer
from sanad.validity import TrustedKeys, TimedSignedPreAuthorization, timed_sign
from sanad.gateway_timed import TimedGateway

TERMS = {"auto_limit_minor": 5000, "daily_budget_minor": 100000,
         "currency": "USD", "blocked_categories": [],
         "valid_days": 7, "approval_ttl_seconds": 300}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Claims:
    def __init__(self):
        self.claimed = {}

    def try_claim(self, approval_id, execution_id):
        if approval_id in self.claimed:
            return False
        self.claimed[approval_id] = execution_id
        return True

    def set_status(self, execution_id, s):
        pass


class Provider:
    def __init__(self):
        self.calls = 0

    def execute(self, amount_minor, currency, execution_id):
        self.calls += 1
        return {"receipt": "re_" + execution_id, "amount_minor": amount_minor}


def build(tmp):
    clock = Clock()
    ledger = Ledger(os.path.join(tmp, "l.jsonl"))
    doc = os.path.join(tmp, "pre_auth.json")
    with open(doc, "w", encoding="utf-8") as f:
        json.dump(TERMS, f)
    m = Signer("mohamed")
    keys = TrustedKeys(ledger, {"mohamed": m.public_key_b64}, clock=clock)
    pre = TimedSignedPreAuthorization(ledger, doc, keys, clock=clock)
    timed_sign(pre, m)
    provider = Provider()
    gw = TimedGateway(ledger, Claims(), pre, provider, clock=clock)
    return clock, ledger, gw, provider


def signed_ttl(ledger):
    """The TTL as SIGNED — read from the ledger snapshot, never from the dict."""
    row = ledger.last(stage="pre_auth", state="SIGNED")
    return row["terms"]["approval_ttl_seconds"]


def test_T1_control_untampered_expired_approval_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        clock, ledger, gw, provider = build(tmp)
        ap = gw.derive_approval("coffee", 3000, "USD")
        assert ap is not None and ap["ttl_seconds"] == 300
        clock.advance(301)
        row = gw.execute(ap)
        assert row["state"] == "DENIED_APPROVAL_EXPIRED"
        assert provider.calls == 0
        assert ap["approval_id"] not in gw.claims.claimed


def test_T2_GAP_ttl_none_on_caller_dict_executes_after_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        clock, ledger, gw, provider = build(tmp)
        ap = gw.derive_approval("coffee", 3000, "USD")
        issued = ap["issued_at"]
        clock.advance(301)
        assert clock() - issued > signed_ttl(ledger)        # truly expired
        ap["ttl_seconds"] = None                            # caller-side edit only
        row = gw.execute(ap)
        assert row["state"] == "EXECUTED"                   # gap
        assert provider.calls == 1                          # money moved
        rec = ledger.last(stage="execute", state="EXECUTED")
        assert rec["receipt"] == row["receipt"]
        assert clock() - issued > signed_ttl(ledger)        # still after expiry


def test_T3_GAP_reset_issued_at_on_caller_dict_executes_after_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        clock, ledger, gw, provider = build(tmp)
        ap = gw.derive_approval("coffee", 3000, "USD")
        issued = ap["issued_at"]
        clock.advance(86_400)                               # one full day later
        assert clock() - issued > signed_ttl(ledger)
        ap["issued_at"] = clock()                           # caller-side edit only
        row = gw.execute(ap)
        assert row["state"] == "EXECUTED"                   # gap
        assert provider.calls == 1
        assert ledger.last(stage="execute", state="EXECUTED") is not None


def test_T4_scope_amount_tamper_still_blocked_by_exp014():
    with tempfile.TemporaryDirectory() as tmp:
        clock, ledger, gw, provider = build(tmp)
        ap = gw.derive_approval("coffee", 3000, "USD")
        ap["amount_minor"] = 999_999
        row = gw.execute(ap)
        assert row["state"] == "DENIED_PAYLOAD_MISMATCH"
        assert provider.calls == 0


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items()
                           if n.startswith("test_")):
        try:
            fn()
            print("PASS", name)
        except Exception as e:  # noqa
            failed += 1
            print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)

```

## test_gap_fg_try_claim.py

```python
# -*- coding: utf-8 -*-
"""F-G — ClaimStore.try_claim() consumes a claim WITHOUT moving the reservation.

STATUS: GAP OBSERVED on the stacked tree (EXP-016 + EXP-017). Not fixed.
Tests PASS while the gap EXISTS; they must flip when a fix lands.

  G1 control : the supported path (begin_send, then recovery) gives the budget back
  G2 gap     : try_claim -> approval burned, reservation stays RESERVED
  G3 gap     : recovery settles the orphan claim NOT_EXECUTED but CANNOT release
               the reservation (its execution_id is NULL): budget stays held
  G4 gap     : verify_reservations does not report the inconsistency

No production code is modified.
"""
import json
import os
import tempfile

from sanad import Ledger, ClaimStore, Gateway, PreAuthorization
from sanad.providers import Provider
from sanad.reconcile import recover_on_startup, verify_reservations


class Witness(Provider):
    name = "memory"

    def __init__(self):
        self.calls = 0

    def execute(self, a, c, e):
        self.calls += 1
        return {"receipt": "rc_" + e, "amount_minor": a}

    def find_by_execution_id(self, e):
        return None

    def retrieve(self, r):
        raise LookupError(r)


def build(tmp):
    ledger = Ledger(os.path.join(tmp, "l.jsonl"))
    claims = ClaimStore(os.path.join(tmp, "c.db"))
    doc = os.path.join(tmp, "pre.json")
    with open(doc, "w", encoding="utf-8") as f:
        json.dump({"auto_limit_minor": 5000, "daily_budget_minor": 5000,
                   "currency": "USD", "blocked_categories": []}, f)
    pre = PreAuthorization(ledger, doc)
    pre.sign("tester")
    prov = Witness()
    return ledger, claims, Gateway(ledger, claims, pre, prov), prov


def test_G1_control_supported_path_releases_after_recovery():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, claims, gw, prov = build(tmp)
        ap = gw.derive_approval("a", 5000, "USD")
        assert claims.begin_send(ap["approval_id"], "EX-1") == "OK"
        recover_on_startup(ledger, claims, prov)          # NOT_EXECUTED
        assert gw.derive_approval("b", 5000, "USD") is not None


def test_G2_GAP_try_claim_burns_the_approval_leaving_reservation_reserved():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, claims, gw, prov = build(tmp)
        ap = gw.derive_approval("a", 5000, "USD")
        assert claims.try_claim(ap["approval_id"], "EX-burn") is True
        row = gw.execute(ap)
        assert row["state"] == "DENIED_APPROVAL_CONSUMED" and prov.calls == 0
        r = claims.reservation_rows()[0]
        assert r["status"] == "RESERVED" and r["execution_id"] is None   # inconsistent


def test_G3_GAP_recovery_settles_the_claim_but_budget_stays_held():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, claims, gw, prov = build(tmp)
        ap = gw.derive_approval("a", 5000, "USD")
        claims.try_claim(ap["approval_id"], "EX-burn")
        settled = recover_on_startup(ledger, claims, prov)
        assert settled == [("EX-burn", "NOT_EXECUTED")]    # claim is settled...
        assert claims.reservation_rows()[0]["status"] == "RESERVED"   # ...hold is not
        assert gw.derive_approval("b", 5000, "USD") is None           # budget stuck
        assert ledger.last(stage="approval")["state"] == "DENIED_DAILY_BUDGET"
        assert prov.calls == 0                              # nothing was ever spent


def test_G4_GAP_verify_reservations_does_not_flag_it():
    with tempfile.TemporaryDirectory() as tmp:
        ledger, claims, gw, prov = build(tmp)
        ap = gw.derive_approval("a", 5000, "USD")
        claims.try_claim(ap["approval_id"], "EX-burn")
        assert verify_reservations(ledger, claims) == []


if __name__ == "__main__":
    import sys
    failed = 0
    for n, f in sorted((n, f) for n, f in globals().items() if n.startswith("test_")):
        try:
            f(); print("PASS", n)
        except Exception as e:  # noqa
            failed += 1; print("FAIL", n, type(e).__name__, e)
    sys.exit(1 if failed else 0)

```
