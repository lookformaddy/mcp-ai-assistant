"""Support code for the end-to-end demo (also used by examples/demo_workflow.py).

Scenario: an operations analyst works a morning queue —
1. checks an order and the stock cover for everything on it (multi-step chain),
2. reviews open critical tickets,
3. pulls the latest revenue summary,
4. tries to create a ticket with a read-only client (refused by the
   client's own policy before anything is sent — defense in depth),
5. repeats with a write-enabled client (allowed, and written to the
   server-side audit log).

Server-side denial auditing (a caller with insufficient scope talking to
the server directly) is covered in tests/test_policy_audit.py.
"""

from __future__ import annotations

import json

from .audit import AuditLog
from .client import AssistantClient, InProcessTransport
from .data import DataStore
from .policy import Scope, ToolPolicy
from .server import MCPServer
from .tools import build_registry


def run_demo() -> None:
    store = DataStore.from_fixtures()
    registry = build_registry(store)
    audit = AuditLog()
    server = MCPServer(
        registry=registry,
        store=store,
        policy=ToolPolicy(granted_scopes={Scope.READ, Scope.WRITE}),
        audit_log=audit,
        caller="demo",
    )

    read_only = AssistantClient(
        InProcessTransport(server), policy=ToolPolicy(granted_scopes={Scope.READ})
    )
    full_access = AssistantClient(
        InProcessTransport(server),
        policy=ToolPolicy(granted_scopes={Scope.READ, Scope.WRITE}),
    )

    requests = [
        ("read-only", read_only, "What is the status of order ORD-1003 and do we have stock cover for its items?"),
        ("read-only", read_only, "Show me the open critical tickets"),
        ("read-only", read_only, "Give me a revenue summary for 2026-Q3"),
        ("read-only", read_only, "Create ticket: Warehouse scanner fleet needs battery replacements"),
        ("full-access", full_access, "Create ticket: Warehouse scanner fleet needs battery replacements"),
    ]

    for label, client, question in requests:
        print("=" * 78)
        print(f"[{label}] Q: {question}")
        answer = client.ask(question)
        print(answer.answer_text)
        if answer.citations:
            print("Citations: " + " ".join(c.render() for c in answer.citations))

    print("=" * 78)
    print(f"AUDIT LOG ({len(audit)} entries):")
    for entry in audit.entries():
        print("  " + json.dumps(entry.model_dump()))
