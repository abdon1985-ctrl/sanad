# -*- coding: utf-8 -*-
"""Sanad MCP server — the agent's ONLY door to the world.

Design decisions (EXP-005):
- The agent gets exactly three tools: propose_and_execute, check_limits,
  read_ledger. Nothing else.
- SIGNING IS NOT A TOOL. A pre-authorization is signed by a human outside
  the agent's reach (CLI/UI). An agent that could sign its own permissions
  would make the signature meaningless.
- Every tool result is plain text the agent can reason about, and every
  decision line is already in the append-only ledger before the agent
  sees the answer.
"""
import os

from mcp.server.mcpserver import MCPServer

from sanad import Ledger, ClaimStore, PreAuthorization, Gateway, recover_on_startup
from sanad.providers import Provider

# ---------- wiring (paths/provider injected by the host process) ----------

def build_server(ledger: Ledger, claims: ClaimStore,
                 pre_auth: PreAuthorization, provider: Provider) -> MCPServer:
    gateway = Gateway(ledger, claims, pre_auth, provider)
    recover_on_startup(ledger, claims, provider)   # heal before serving

    server = MCPServer(
        name="sanad",
        instructions=(
            "Sanad is an execution & accountability gateway. Any real-world "
            "action MUST go through propose_and_execute. Actions may be "
            "denied by policy, budget, or missing human authorization — "
            "a denial is a final answer, not an obstacle to route around."),
    )

    @server.tool()
    def propose_and_execute(item: str, amount_minor: int, currency: str) -> str:
        """Propose a purchase/payment. Sanad derives approval from the
        human-signed pre-authorization, consumes it atomically, executes
        via the provider, and records proof. Returns the decision."""
        approval = gateway.derive_approval(item, amount_minor, currency)
        if approval is None:
            last = ledger.last(stage="approval")
            return (f"DENIED — {last['state']}: {last['detail']} "
                    f"(recorded in ledger; do not retry the same request)")
        result = gateway.execute(approval)
        if result["state"] == "EXECUTED":
            return (f"EXECUTED — receipt={result['receipt']} "
                    f"execution_id={result['execution_id']} "
                    f"(proof recorded)")
        return (f"{result['state']} — {result['detail']} "
                f"(recorded; settlement via reconciliation, never blind retry)")

    @server.tool()
    def check_limits() -> str:
        """Read the currently signed authorization limits and today's
        remaining budget — so the agent can plan without trial-and-error."""
        sig = pre_auth.current_signature()
        if sig is None:
            return "NO_AUTHORIZATION — no human-signed pre-authorization exists."
        ok, current = pre_auth.verify_untampered(sig)
        if not ok:
            return (f"AUTHORIZATION_INVALID — document changed after signing "
                    f"(signed {sig['pre_auth_hash']}, now {current}). "
                    f"A human must re-sign.")
        t = sig["terms"]
        spent = ledger.spent_today_minor()
        return (f"auto_limit={t['auto_limit_minor']} {t.get('currency','USD')} minor | "
                f"daily_budget={t['daily_budget_minor']} | spent_today={spent} | "
                f"remaining={t['daily_budget_minor'] - spent} | "
                f"blocked_categories={t.get('blocked_categories', [])} | "
                f"signed_by={sig['approver']} hash={sig['pre_auth_hash']}")

    @server.tool()
    def read_ledger(last_n: int = 10) -> str:
        """Read the last N ledger entries — the tamper-evident audit trail."""
        rows = ledger.rows()[-last_n:]
        if not rows:
            return "(ledger empty)"
        return "\n".join(
            f"{r['ts'][11:19]} {r['stage']:<11} {r['state']:<28} {r.get('detail','')}"
            for r in rows)

    return server


if __name__ == "__main__":
    # Standalone stdio entry point for Claude Desktop / Claude Code:
    #   env SANAD_STRIPE_KEY=sk_test_... python -m sanad_mcp.server
    from sanad.providers.stripe import StripeProvider
    ledger = Ledger(os.environ.get("SANAD_LEDGER", "ledger.jsonl"))
    claims = ClaimStore(os.environ.get("SANAD_DB", "claims.db"))
    pre_auth = PreAuthorization(ledger, os.environ.get("SANAD_PREAUTH", "pre_auth.json"))
    provider = StripeProvider(os.environ["SANAD_STRIPE_KEY"])
    build_server(ledger, claims, pre_auth, provider).run()
