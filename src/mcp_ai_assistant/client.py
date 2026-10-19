"""Assistant client: discovers tools, plans calls, executes chains.

The client talks to an :class:`MCPServer` through a *transport* (in-process
for tests/demos, HTTP for real deployments). A *planner* converts a natural
language request into a list of tool-call steps:

* :class:`RuleBasedPlanner` — deterministic, offline, regex-driven. This is
  the fallback that always works with no model access.
* :class:`LLMPlanner` — wraps any ``str -> str`` completion callable,
  asks it for a JSON plan, validates it against the discovered tool
  schemas, and falls back to the rule-based planner on any failure.

Steps can *fan out*: a step may declare ``expand_from`` pointing at a field
of a previous step's result (e.g. the SKUs on an order), producing one call
per value — that is how "check the order and the stock for everything on
it" becomes a multi-step chain.

The client enforces the same policy model as the server (defense in
depth): a step whose tool the client's policy does not permit is refused
before it is ever sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .errors import MCPError, PermissionDeniedError
from .policy import Scope, ToolPolicy

# ---------------------------------------------------------------------------
# Plan model
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    # Optional fan-out: {"step": <index>, "path": "orders.0.items", "field": "sku",
    #                   "argument": "sku"}
    expand_from: dict[str, Any] | None = None


@dataclass
class Citation:
    tool: str
    records: list[str]

    def render(self) -> str:
        return f"[{self.tool}: {', '.join(self.records)}]"


@dataclass
class StepResult:
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AssistantAnswer:
    request: str
    answer_text: str
    steps: list[StepResult]
    citations: list[Citation]
    denied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "answer": self.answer_text,
            "citations": [c.render() for c in self.citations],
            "steps": [
                {"tool": s.tool, "arguments": s.arguments, "result": s.result}
                for s in self.steps
            ],
            "denied": self.denied,
        }


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Transport(Protocol):
    def request(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


class InProcessTransport:
    """Directly dispatches to a local MCPServer (tests, demos, embedding)."""

    def __init__(self, server: Any):
        self._server = server
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        response = self._server.handle(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        )
        if response and "error" in response:
            err = response["error"]
            raise MCPError(err["code"], err["message"], err.get("data") or {})
        return response["result"] if response else None


class HTTPTransport:
    """Talks to the FastAPI transport (``POST /rpc``) with httpx."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        resp = self._client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}},
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload and payload["error"]:
            err = payload["error"]
            raise MCPError(err["code"], err["message"], err.get("data") or {})
        return payload.get("result")


# ---------------------------------------------------------------------------
# Planners
# ---------------------------------------------------------------------------


class Planner(Protocol):
    def plan(self, request_text: str, tools: list[dict[str, Any]]) -> list[PlanStep]: ...


_ORDER_RE = re.compile(r"\b(ORD-\d+)\b", re.IGNORECASE)
_SKU_RE = re.compile(r"\b(SKU-\d+)\b", re.IGNORECASE)
_PERIOD_RE = re.compile(r"\b(\d{4}-Q[1-4])\b", re.IGNORECASE)
_REGION_RE = re.compile(r"\b(NA|EU|APAC)\b")
_PRIORITY_WORDS = ("critical", "high", "medium", "low")
_STATUS_WORDS = {"open": "open", "in progress": "in_progress", "resolved": "resolved", "closed": "closed"}


class RuleBasedPlanner:
    """Deterministic offline planner driven by request patterns."""

    def plan(self, request_text: str, tools: list[dict[str, Any]]) -> list[PlanStep]:
        text = request_text.strip()
        lower = text.lower()
        available = {t["name"] for t in tools}
        steps: list[PlanStep] = []

        def add(step: PlanStep) -> None:
            if step.tool in available:
                steps.append(step)

        # Create-ticket intent (write) — check first; "ticket" also matches search.
        if re.search(r"\b(create|open|file|raise)\b.*\bticket\b", lower):
            title_match = re.search(r"ticket[:\s]+[\"']?(.+?)[\"']?$", text, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else text
            priority = next((p for p in _PRIORITY_WORDS if p in lower), "medium")
            add(PlanStep("create_ticket", {"title": title, "priority": priority}))
            return steps

        # Order intent, optionally chained into inventory checks.
        order_match = _ORDER_RE.search(text)
        if order_match or ("order" in lower and "status" in lower):
            args = {"order_id": order_match.group(1).upper()} if order_match else {}
            if args:
                add(PlanStep("order_lookup", args))
                if any(w in lower for w in ("stock", "inventory", "cover", "available")):
                    add(
                        PlanStep(
                            "inventory_check",
                            {},
                            expand_from={
                                "step": 0,
                                "path": "orders.0.items",
                                "field": "sku",
                                "argument": "sku",
                            },
                        )
                    )
                return steps

        # Inventory intent.
        sku_match = _SKU_RE.search(text)
        if sku_match or any(w in lower for w in ("inventory", "stock")):
            if sku_match:
                add(PlanStep("inventory_check", {"sku": sku_match.group(1).upper()}))
            elif "low" in lower or "reorder" in lower:
                add(PlanStep("inventory_check", {"product_query": "a"}))  # broad scan, filtered in answer
            else:
                query = re.sub(r".*(?:for|of)\s+", "", text, flags=re.IGNORECASE).strip(" ?.\"")
                add(PlanStep("inventory_check", {"product_query": query or "scanner"}))
            return steps

        # Ticket search intent.
        if "ticket" in lower:
            args: dict[str, Any] = {}
            for word, status in _STATUS_WORDS.items():
                if word in lower:
                    args["status"] = status
                    break
            priority = next((p for p in _PRIORITY_WORDS if re.search(rf"\b{p}\b", lower)), None)
            if priority:
                args["priority"] = priority
            query_match = re.search(r"tickets?\s+(?:about|for|on)\s+(.+)$", text, re.IGNORECASE)
            if query_match:
                args["query"] = query_match.group(1).strip(" ?.\"")
            add(PlanStep("ticket_search", args))
            return steps

        # Metrics intent.
        if any(w in lower for w in ("metric", "revenue", "sales", "summary", "performance", "orders")):
            args = {}
            if "revenue" in lower or "sales" in lower:
                args["metric"] = "revenue"
            elif "return" in lower:
                args["metric"] = "return_rate_pct"
            elif "average order" in lower or "avg" in lower:
                args["metric"] = "avg_order_value"
            elif "orders" in lower:
                args["metric"] = "orders_count"
            region = _REGION_RE.search(text)
            if region:
                args["region"] = region.group(1)
            period = _PERIOD_RE.search(text)
            if period:
                args["period"] = period.group(1).upper()
            add(PlanStep("metric_summary", args))
            return steps

        return steps


class LLMPlanner:
    """Pluggable planner: any ``prompt -> completion`` callable.

    The callable must return a JSON array of ``{"tool": ..., "arguments": {...}}``
    objects. Anything unparseable or off-catalog falls back to the
    deterministic rule-based planner, so behavior stays predictable
    offline.
    """

    def __init__(self, completion: Callable[[str], str], fallback: Planner | None = None):
        self._completion = completion
        self._fallback = fallback or RuleBasedPlanner()

    def plan(self, request_text: str, tools: list[dict[str, Any]]) -> list[PlanStep]:
        catalog = json.dumps(
            [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in tools],
            indent=2,
        )
        prompt = (
            "You are a tool planner. Given the tool catalog and the user request, "
            "return ONLY a JSON array of steps like "
            '[{"tool": "order_lookup", "arguments": {"order_id": "ORD-1001"}}].\n\n'
            f"Tool catalog:\n{catalog}\n\nUser request: {request_text}"
        )
        try:
            raw = self._completion(prompt)
            parsed = json.loads(raw)
            available = {t["name"] for t in tools}
            steps = [
                PlanStep(tool=s["tool"], arguments=s.get("arguments") or {})
                for s in parsed
                if isinstance(s, dict) and s.get("tool") in available
            ]
            if steps:
                return steps
        except Exception:
            pass
        return self._fallback.plan(request_text, tools)


# ---------------------------------------------------------------------------
# Assistant client
# ---------------------------------------------------------------------------


class AssistantClient:
    def __init__(
        self,
        transport: Transport,
        planner: Planner | None = None,
        policy: ToolPolicy | None = None,
    ):
        self._transport = transport
        self._planner = planner or RuleBasedPlanner()
        self._policy = policy or ToolPolicy(granted_scopes={Scope.READ, Scope.WRITE})
        self._tools: list[dict[str, Any]] | None = None
        self._scopes: dict[str, Scope] = {}

    # -- discovery ---------------------------------------------------------
    def discover_tools(self) -> list[dict[str, Any]]:
        result = self._transport.request("tools/list")
        self._tools = result["tools"]
        self._scopes = {
            t["name"]: Scope(t.get("annotations", {}).get("scope", "read")) for t in self._tools
        }
        return self._tools

    # -- main entry point ---------------------------------------------------
    def ask(self, request_text: str) -> AssistantAnswer:
        if self._tools is None:
            self.discover_tools()
        assert self._tools is not None
        plan = self._planner.plan(request_text, self._tools)

        executed: list[StepResult] = []
        denied: list[str] = []
        raw_results: list[dict[str, Any]] = []

        for step in plan:
            calls = self._expand(step, raw_results)
            for call_args in calls:
                scope = self._scopes.get(step.tool, Scope.READ)
                try:
                    self._policy.check(step.tool, scope)
                except PermissionDeniedError as exc:
                    denied.append(exc.message)
                    continue
                result = self._transport.request(
                    "tools/call", {"name": step.tool, "arguments": call_args}
                )
                structured = result.get("structuredContent", {}) if isinstance(result, dict) else {}
                executed.append(StepResult(tool=step.tool, arguments=call_args, result=structured))
                raw_results.append(structured)

        citations = _collect_citations(executed)
        answer_text = _compose_answer(request_text, executed, denied)
        return AssistantAnswer(
            request=request_text,
            answer_text=answer_text,
            steps=executed,
            citations=citations,
            denied=denied,
        )

    # -- fan-out --------------------------------------------------------------
    def _expand(self, step: PlanStep, prior_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not step.expand_from:
            return [step.arguments]
        spec = step.expand_from
        source = prior_results[spec["step"]] if spec["step"] < len(prior_results) else {}
        items = _dig(source, spec["path"])
        calls = []
        for item in items or []:
            value = item.get(spec["field"]) if isinstance(item, dict) else None
            if value:
                calls.append({**step.arguments, spec["argument"]: value})
        return calls


def _dig(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _collect_citations(steps: list[StepResult]) -> list[Citation]:
    citations: list[Citation] = []
    for step in steps:
        records: list[str] = []
        result = step.result
        for order in result.get("orders", []) or []:
            records.append(order["id"])
        for item in result.get("inventory", []) or []:
            records.append(item["sku"])
        for ticket in result.get("tickets", []) or []:
            records.append(ticket["id"])
        if result.get("ticket"):
            records.append(result["ticket"]["id"])
        for row in result.get("rows", []) or []:
            records.append(f"{row['metric']}@{row['region']}/{row['period']}")
        if records:
            citations.append(Citation(tool=step.tool, records=records))
    return citations


def _compose_answer(request_text: str, steps: list[StepResult], denied: list[str]) -> str:
    if not steps and not denied:
        return (
            "I could not map that request to an available tool. Try asking about an "
            "order (e.g. 'status of order ORD-1001'), inventory ('stock for SKU-2003'), "
            "tickets ('open critical tickets'), or metrics ('revenue summary for 2026-Q3')."
        )
    lines: list[str] = []
    for step in steps:
        r = step.result
        if step.tool == "order_lookup":
            if not r.get("found"):
                lines.append(f"No order found for {step.arguments}.")
            for order in r.get("orders", []):
                items = ", ".join(f"{i['sku']} x{i['quantity']}" for i in order.get("items", []))
                lines.append(
                    f"Order {order['id']} ({order['customer']}): {order['status']}, "
                    f"total {order['total']} {order['currency']}, placed {order['placed_at']}"
                    + (f"; items: {items}." if items else ".")
                )
        elif step.tool == "inventory_check":
            if not r.get("found"):
                lines.append(f"No inventory found for {step.arguments}.")
            for item in r.get("inventory", []):
                flag = " — LOW STOCK" if item["low_stock"] else ""
                lines.append(
                    f"{item['sku']} ({item['product']}, {item['warehouse']}): "
                    f"{item['available']} available of {item['on_hand']} on hand "
                    f"({item['reserved']} reserved), reorder point {item['reorder_point']}{flag}."
                )
        elif step.tool == "ticket_search":
            lines.append(f"Found {r.get('count', 0)} ticket(s):")
            for t in r.get("tickets", []):
                lines.append(
                    f"- {t['id']} [{t['priority']}/{t['status']}] {t['title']} ({t['assignee']})"
                )
        elif step.tool == "metric_summary":
            for row in r.get("rows", []):
                lines.append(f"{row['metric']} {row['region']} {row['period']}: {row['value']}")
            if "total_revenue" in r:
                lines.append(f"Total revenue across selected rows: {r['total_revenue']}.")
            if "period_over_period_change_pct" in r:
                lines.append(f"Period-over-period revenue change: {r['period_over_period_change_pct']}%.")
        elif step.tool == "create_ticket":
            t = r.get("ticket", {})
            lines.append(f"Created ticket {t.get('id')}: '{t.get('title')}' (priority {t.get('priority')}).")
        else:
            lines.append(f"{step.tool}: {json.dumps(r)}")
    for message in denied:
        lines.append(f"Not executed — {message}")
    return "\n".join(lines)
