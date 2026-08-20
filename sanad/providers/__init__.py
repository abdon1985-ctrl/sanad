# -*- coding: utf-8 -*-
"""Provider interface.

A provider is the external world: it executes a committed action and can be
queried for evidence. Rules the gateway relies on (EXP-001/003):
- execute() must accept an idempotency key derived from the execution_id.
- find_by_execution_id() must answer from the provider's own records —
  reconciliation is settled by reality, not by assumption.
"""


class Provider:
    name = "abstract"

    def execute(self, amount_minor: int, currency: str, execution_id: str) -> dict:
        """Perform the action. Returns {'receipt': ..., 'amount_minor': ...}.
        Raises ProviderRejected on explicit refusal; any other exception
        means UNKNOWN (the response did not arrive)."""
        raise NotImplementedError

    def find_by_execution_id(self, execution_id: str):
        """Return a receipt dict if the provider holds a record for this
        execution_id, else None. Used by reconcile."""
        raise NotImplementedError

    def retrieve(self, receipt: str):
        """Direct lookup by receipt id (no search-index lag). Optional."""
        raise NotImplementedError


class ProviderRejected(Exception):
    """The provider explicitly refused (e.g. HTTP 4xx) — a definite outcome."""
