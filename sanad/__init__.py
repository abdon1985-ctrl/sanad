# -*- coding: utf-8 -*-
"""Sanad (سند) — an execution & accountability gateway for AI agents.

Autonomy is not the absence of approval; it is a pre-signed approval.
Every action answers four questions: Who authorized it? Under which exact
terms? What actually happened? And can we prove it afterwards?
"""
from .ledger import Ledger
from .policy import PreAuthorization, read_document
from .claims import ClaimStore
from .gateway import Gateway
from .reconcile import recover_on_startup
from .providers import Provider, ProviderRejected

__version__ = "0.1.0"
__all__ = ["Ledger", "PreAuthorization", "read_document", "ClaimStore",
           "Gateway", "recover_on_startup", "Provider", "ProviderRejected"]
