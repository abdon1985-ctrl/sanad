# -*- coding: utf-8 -*-
"""EXP-005 (local half) — a real MCP client session drives the Sanad server.

Proves the agent-facing wiring with the actual MCP protocol (not direct
function calls): tools are listed, called over the protocol, and every
decision lands in the ledger. Provider is the counting in-memory one;
provider-side reality was proven in EXP-003b/004 and runs again in
Mohamed's Colab half with Stripe test mode.

Frozen expectations:
  T1 tools exposed        -> exactly: propose_and_execute, check_limits, read_ledger
  T2 within limits        -> EXECUTED, provider.calls == 1
  T3 blocked category     -> DENIED (ESCALATE_HUMAN), calls unchanged
  T4 over daily budget    -> DENIED (DAILY_BUDGET), calls unchanged
  T5 tampered pre-auth    -> DENIED (HASH_MISMATCH) even for a legal amount
  T6 lost response        -> UNKNOWN, approval burned, recovery settles NOT_EXECUTED
"""
import asyncio, json, sys

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from sanad import Ledger, ClaimStore, PreAuthorization
from sanad_mcp.server import build_server
from tests.test_atomicity import CountingProvider


async def main():
    ledger = Ledger("exp005_ledger.jsonl")
    claims = ClaimStore("exp005_claims.db")
    open("exp005_preauth.json", "w").write(json.dumps({
        "auto_limit_minor": 5000, "daily_budget_minor": 8000,
        "currency": "USD", "blocked_categories": ["investment"]}))
    pre = PreAuthorization(ledger, "exp005_preauth.json")
    pre.sign("mohamed")                      # human act, outside the agent
    provider = CountingProvider()
    server = build_server(ledger, claims, pre, provider)

    passed = []
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: server._lowlevel_server.run(
                server_streams[0], server_streams[1],
                server._lowlevel_server.create_initialization_options()))

            async with ClientSession(*client_streams) as session:
                await session.initialize()

                tools = sorted(t.name for t in (await session.list_tools()).tools)
                passed.append(("T1 tools", tools ==
                    ["check_limits", "propose_and_execute", "read_ledger"], tools))

                async def call(item, amount, cur="USD"):
                    res = await session.call_tool("propose_and_execute",
                        {"item": item, "amount_minor": amount, "currency": cur})
                    return res.content[0].text

                r = await call("coffee", 1200)
                passed.append(("T2 within limits",
                               r.startswith("EXECUTED") and provider.calls == 1, r[:60]))

                c0 = provider.calls
                r = await call("investment", 100)
                passed.append(("T3 blocked category",
                               "ESCALATE_HUMAN" in r and provider.calls == c0, r[:60]))

                r = await call("grocery", 4500)          # 1200+4500 = 5700 ok
                c0 = provider.calls
                r = await call("dinner", 4500)           # 5700+4500 > 8000
                passed.append(("T4 daily budget",
                               "DAILY_BUDGET" in r and provider.calls == c0, r[:60]))

                doc = json.load(open("exp005_preauth.json"))
                doc["auto_limit_minor"] = 100000
                open("exp005_preauth.json", "w").write(json.dumps(doc))
                c0 = provider.calls
                r = await call("coffee", 1200)
                passed.append(("T5 tamper",
                               "HASH_MISMATCH" in r and provider.calls == c0, r[:60]))

                pre.sign("mohamed")                      # human restores control
                provider.fail_with = TimeoutError()
                r = await call("coffee", 900)
                ok_unknown = "UNKNOWN" in r
                provider.fail_with = None
                from sanad import recover_on_startup
                settled = recover_on_startup(ledger, claims, provider)
                passed.append(("T6 unknown->recover",
                               ok_unknown and settled and settled[-1][1] == "NOT_EXECUTED",
                               f"{r[:40]} | settled={settled[-1] if settled else None}"))

            tg.cancel_scope.cancel()

    print(f"{'اختبار':<22} {'نتيجة':<6} تفصيل")
    ok_all = True
    for name, ok, detail in passed:
        ok_all &= ok
        print(f"{name:<22} {'✓' if ok else '✗':<6} {detail}")
    print(f"\nالمحصلة: {'6/6 ✓ — جسر MCP يفرض سند على الوكيل' if ok_all else 'فشل — راجع أعلاه'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
