#!/usr/bin/env python3
"""End-to-end workflow demo.

Runs an operations-analyst scenario against an in-process server:
multi-step tool chains (order -> stock cover), ticket triage, a metrics
summary, a policy denial for a read-only caller, and a successful write
with a write-enabled caller. The full audit log prints at the end.

Usage:
    python examples/demo_workflow.py
    # or, from an installed checkout:
    mcp-ai-assistant demo
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_ai_assistant.examples_support import run_demo

if __name__ == "__main__":
    run_demo()
