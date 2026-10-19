"""MCP-style tool server.

Transport-agnostic core (``MCPServer.handle``) speaking JSON-RPC 2.0, with
two transports:

* **stdio** — line-delimited JSON-RPC over stdin/stdout (the classic MCP
  local transport; logs go to stderr so stdout stays a clean channel).
* **HTTP** — FastAPI app exposing ``POST /rpc`` plus convenience
  endpoints (``GET /health``, ``GET /tools``, ``GET /audit``).

Every ``tools/call`` passes through the policy gate and is written to the
audit log — allowed, denied, and failed calls alike.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, TextIO

from fastapi import FastAPI, Header, HTTPException, Request

from . import PROTOCOL_VERSION, SERVER_NAME, __version__
from .audit import AuditEntry, AuditLog
from .errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PROMPT_NOT_FOUND,
    RESOURCE_NOT_FOUND,
    MCPError,
    PermissionDeniedError,
)
from .policy import ToolPolicy
from .protocol import error_response, parse_request, success_response
from .registry import ToolRegistry

# ---------------------------------------------------------------------------
# Resources & prompts (static content served alongside tools)
# ---------------------------------------------------------------------------

RESOURCE_DEFS: list[dict[str, str]] = [
    {
        "uri": "enterprise://orders/recent",
        "name": "Recent orders",
        "description": "The five most recently placed orders with line items.",
        "mimeType": "application/json",
    },
    {
        "uri": "enterprise://inventory/low-stock",
        "name": "Low-stock inventory",
        "description": "Items whose available stock is at or below the reorder point.",
        "mimeType": "application/json",
    },
    {
        "uri": "enterprise://metrics/latest-quarter",
        "name": "Latest-quarter metrics",
        "description": "All business metrics for the most recent period on file.",
        "mimeType": "application/json",
    },
]

PROMPT_DEFS: list[dict[str, Any]] = [
    {
        "name": "order_investigation",
        "description": "Investigate an order end-to-end: status, line items, and stock cover.",
        "arguments": [
            {"name": "order_id", "description": "Order ID, e.g. ORD-1001", "required": True}
        ],
    },
    {
        "name": "weekly_ops_review",
        "description": "Draft a weekly operations review: revenue, open critical tickets, low stock.",
        "arguments": [
            {"name": "region", "description": "Region filter: NA, EU, or APAC", "required": False}
        ],
    },
]


class MCPServer:
    def __init__(
        self,
        registry: ToolRegistry,
        store: Any,
        policy: ToolPolicy | None = None,
        audit_log: AuditLog | None = None,
        caller: str = "anonymous",
    ):
        self.registry = registry
        self.store = store
        self.policy = policy if policy is not None else ToolPolicy()
        # NB: `audit_log or AuditLog()` would be a bug — AuditLog defines
        # __len__, so an empty log is falsy and would be silently replaced.
        self.audit = audit_log if audit_log is not None else AuditLog()
        self.caller = caller

    # -- JSON-RPC dispatch -------------------------------------------------
    def handle(self, raw: Any) -> dict | None:
        """Handle one decoded JSON-RPC payload; returns a response dict,
        or None for notifications."""
        try:
            request = parse_request(raw)
        except MCPError as exc:
            return error_response(raw.get("id") if isinstance(raw, dict) else None, exc)

        handler = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": self._prompts_list,
            "prompts/get": self._prompts_get,
        }.get(request.method)

        if handler is None:
            if request.is_notification:
                return None
            return error_response(
                request.id, MCPError(METHOD_NOT_FOUND, f"Unknown method: {request.method}")
            )
        try:
            result = handler(request.params)
        except MCPError as exc:
            return None if request.is_notification else error_response(request.id, exc)
        except Exception as exc:  # pragma: no cover - defensive
            err = MCPError(INTERNAL_ERROR, f"Internal error: {exc}")
            return None if request.is_notification else error_response(request.id, err)
        if request.is_notification:
            return None
        return success_response(request.id, result)

    # -- method handlers -----------------------------------------------------
    def _initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }

    def _ping(self, params: dict) -> dict:
        return {}

    def _tools_list(self, params: dict) -> dict:
        return {"tools": self.registry.list_tools()}

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name or not isinstance(name, str):
            raise MCPError(INVALID_PARAMS, "tools/call requires a string 'name'")
        if not isinstance(arguments, dict):
            raise MCPError(INVALID_PARAMS, "tools/call 'arguments' must be an object")

        definition = self.registry.get(name)  # raises ToolNotFoundError
        started = time.perf_counter()
        try:
            self.policy.check(name, definition.scope)
        except PermissionDeniedError as exc:
            self.audit.record(
                AuditEntry(
                    caller=self.caller, tool=name, scope=definition.scope.value,
                    arguments=arguments, status="denied", error=exc.message,
                )
            )
            raise
        try:
            result = self.registry.call(name, arguments)
        except MCPError as exc:
            self.audit.record(
                AuditEntry(
                    caller=self.caller, tool=name, scope=definition.scope.value,
                    arguments=arguments, status="error",
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    error=exc.message,
                )
            )
            raise
        duration = round((time.perf_counter() - started) * 1000, 3)
        self.audit.record(
            AuditEntry(
                caller=self.caller, tool=name, scope=definition.scope.value,
                arguments=arguments, status="ok", duration_ms=duration,
                result_summary=_summarize_result(result),
            )
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "structuredContent": result,
            "isError": False,
        }

    def _resources_list(self, params: dict) -> dict:
        return {"resources": RESOURCE_DEFS}

    def _resources_read(self, params: dict) -> dict:
        uri = params.get("uri")
        payload: Any
        if uri == "enterprise://orders/recent":
            payload = self.store.recent_orders(limit=5)
        elif uri == "enterprise://inventory/low-stock":
            payload = self.store.low_stock()
        elif uri == "enterprise://metrics/latest-quarter":
            rows = self.store.metrics()
            latest = max((r["period"] for r in rows), default=None)
            payload = [r for r in rows if r["period"] == latest]
        else:
            raise MCPError(RESOURCE_NOT_FOUND, f"Unknown resource: {uri}", {"uri": uri})
        return {
            "contents": [
                {"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}
            ]
        }

    def _prompts_list(self, params: dict) -> dict:
        return {"prompts": PROMPT_DEFS}

    def _prompts_get(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "order_investigation":
            order_id = arguments.get("order_id", "ORD-????")
            text = (
                f"Investigate order {order_id}: (1) call order_lookup for the order and its "
                "line items; (2) for each line item, call inventory_check to confirm stock "
                "cover; (3) summarize status, risks, and next actions with citations."
            )
            description = f"Order investigation plan for {order_id}"
        elif name == "weekly_ops_review":
            region = arguments.get("region")
            scope = f" for region {region}" if region else ""
            text = (
                f"Prepare the weekly operations review{scope}: (1) metric_summary for revenue "
                "and orders_count; (2) ticket_search for open critical and high-priority "
                "tickets; (3) review the low-stock resource; (4) draft highlights, risks, "
                "and owners with citations."
            )
            description = "Weekly operations review plan"
        else:
            raise MCPError(PROMPT_NOT_FOUND, f"Unknown prompt: {name}", {"prompt": name})
        return {
            "description": description,
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ],
        }


def _summarize_result(result: dict) -> str:
    keys = ", ".join(sorted(result.keys())[:6])
    return f"keys: {keys}"


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------

def run_stdio(server: MCPServer, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Serve line-delimited JSON-RPC over stdio until EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            from .errors import PARSE_ERROR

            response = error_response(None, MCPError(PARSE_ERROR, f"Parse error: {exc}"))
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue
        response = server.handle(raw)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


# ---------------------------------------------------------------------------
# HTTP transport (FastAPI)
# ---------------------------------------------------------------------------

def create_app(server: MCPServer):  # returns a FastAPI app
    # Imported at module level (below) so FastAPI can resolve the
    # `request: Request` annotation — a function-local import combined
    # with `from __future__ import annotations` makes FastAPI treat the
    # parameter as a query field and every POST fails with 422.
    app = FastAPI(title="MCP AI Assistant", version=__version__)
    expected_token = os.environ.get("MCP_AI_API_TOKEN")

    def _check_auth(authorization: str | None) -> None:
        if expected_token and authorization != f"Bearer {expected_token}":
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "server": SERVER_NAME, "version": __version__}

    @app.get("/tools")
    def tools(authorization: str | None = Header(default=None)) -> dict:
        _check_auth(authorization)
        return {"tools": server.registry.list_tools()}

    @app.get("/audit")
    def audit(
        tool: str | None = None,
        status: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_auth(authorization)
        entries = server.audit.entries(tool=tool, status=status)
        return {"count": len(entries), "entries": [e.model_dump() for e in entries]}

    @app.post("/rpc")
    async def rpc(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_auth(authorization)
        try:
            raw = await request.json()
        except Exception:
            from .errors import PARSE_ERROR

            return error_response(None, MCPError(PARSE_ERROR, "Request body is not valid JSON"))
        response = server.handle(raw)
        return response or {"jsonrpc": "2.0", "id": None, "result": {}}

    return app
