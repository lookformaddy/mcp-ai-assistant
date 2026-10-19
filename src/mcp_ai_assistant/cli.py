"""Command-line entry points.

Usage:
    mcp-ai-assistant serve --transport stdio
    mcp-ai-assistant serve --transport http --port 8000
    mcp-ai-assistant ask "status of order ORD-1001 and check stock cover"
    mcp-ai-assistant demo
"""

from __future__ import annotations

import argparse
import json
import os

from .audit import AuditLog
from .client import AssistantClient, InProcessTransport
from .data import DataStore
from .policy import Scope, ToolPolicy
from .server import MCPServer, create_app, run_stdio
from .tools import build_registry


def build_server(
    scopes: set[Scope] | None = None,
    audit_path: str | None = None,
    caller: str = "cli",
) -> MCPServer:
    store = DataStore.from_fixtures()
    registry = build_registry(store)
    policy = ToolPolicy(granted_scopes=scopes or {Scope.READ, Scope.WRITE})
    audit = AuditLog(path=audit_path or os.environ.get("MCP_AI_AUDIT_LOG"))
    return MCPServer(registry=registry, store=store, policy=policy, audit_log=audit, caller=caller)


def _cmd_serve(args: argparse.Namespace) -> int:
    scopes = {Scope.READ} if args.read_only else {Scope.READ, Scope.WRITE}
    server = build_server(scopes=scopes, audit_path=args.audit_log, caller="server")
    if args.transport == "stdio":
        run_stdio(server)
    else:
        import uvicorn

        uvicorn.run(create_app(server), host=args.host, port=args.port)
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    server = build_server(caller="cli-ask")
    client = AssistantClient(InProcessTransport(server))
    answer = client.ask(args.question)
    print(answer.answer_text)
    if answer.citations:
        print("\nCitations: " + " ".join(c.render() for c in answer.citations))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from .examples_support import run_demo

    run_demo()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-ai-assistant", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP tool server")
    serve.add_argument("--transport", choices=["stdio", "http"], default="http")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--read-only", action="store_true", help="Grant only the read scope")
    serve.add_argument("--audit-log", default=None, help="Path to a JSONL audit log file")
    serve.set_defaults(func=_cmd_serve)

    ask = sub.add_parser("ask", help="Ask the assistant a question (in-process server)")
    ask.add_argument("question")
    ask.set_defaults(func=_cmd_ask)

    demo = sub.add_parser("demo", help="Run the end-to-end workflow demo")
    demo.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
