#!/usr/bin/env python3
"""Raw JSON-RPC over stdio: spawn the server as a subprocess and speak
the protocol by hand — the same way an MCP host (IDE, agent runtime)
would. Shows initialize, tools/list, tools/call, and a denied write when
the server is launched --read-only.

Usage:
    python examples/stdio_session.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_ai_assistant.cli", "serve", "--transport", "stdio", "--read-only"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.stdin and proc.stdout

    def call(req_id: int, method: str, params: dict | None = None) -> dict:
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}) + "\n"
        )
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    print("initialize ->", json.dumps(call(1, "initialize", {"protocolVersion": "2024-11-05"})["result"]["serverInfo"]))
    tools = call(2, "tools/list")["result"]["tools"]
    print("tools      ->", [t["name"] for t in tools])
    ok = call(3, "tools/call", {"name": "order_lookup", "arguments": {"order_id": "ORD-1006"}})
    print("order      ->", ok["result"]["structuredContent"]["orders"][0]["status"])
    denied = call(4, "tools/call", {"name": "create_ticket", "arguments": {"title": "Replace dock door sensor"}})
    print("write      -> ERROR", denied["error"]["code"], denied["error"]["message"])
    proc.stdin.close()
    proc.wait(timeout=10)


if __name__ == "__main__":
    main()
