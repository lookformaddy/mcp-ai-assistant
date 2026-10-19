"""Built-in tools over the synthetic enterprise data store.

Four read tools and one write tool. The write tool (``create_ticket``)
exists deliberately: it demonstrates scope enforcement — read-only callers
are denied by policy before the handler ever runs.
"""

from __future__ import annotations

from typing import Any

from .data import DataStore
from .policy import Scope
from .registry import ToolDefinition, ToolRegistry


def _with_availability(row: dict[str, Any]) -> dict[str, Any]:
    available = row["on_hand"] - row["reserved"]
    return {
        **row,
        "available": available,
        "low_stock": available <= row["reorder_point"],
    }


def build_registry(store: DataStore) -> ToolRegistry:
    registry = ToolRegistry()

    # --- order_lookup (read) --------------------------------------------
    def order_lookup(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("order_id"):
            order = store.get_order(args["order_id"].upper())
            if order is None:
                return {"found": False, "order_id": args["order_id"], "orders": []}
            return {"found": True, "orders": [order]}
        orders = store.orders_for_customer(args["customer"])
        return {"found": len(orders) > 0, "orders": orders}

    registry.register(
        ToolDefinition(
            name="order_lookup",
            description=(
                "Look up order(s) by exact order ID (e.g. 'ORD-1001') or by a "
                "customer name fragment. Returns status, totals, and line items."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^ORD-\\d+$"},
                    "customer": {"type": "string", "minLength": 2},
                },
                "anyOf": [{"required": ["order_id"]}, {"required": ["customer"]}],
                "additionalProperties": False,
            },
            scope=Scope.READ,
        ),
        order_lookup,
    )

    # --- inventory_check (read) ------------------------------------------
    def inventory_check(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("sku"):
            row = store.get_inventory(args["sku"].upper())
            rows = [row] if row else []
        else:
            rows = store.search_inventory(args["product_query"])
        return {"found": len(rows) > 0, "inventory": [_with_availability(r) for r in rows]}

    registry.register(
        ToolDefinition(
            name="inventory_check",
            description=(
                "Check stock for a SKU (e.g. 'SKU-2001') or search products by "
                "name fragment. Returns on-hand, reserved, available, and a "
                "low-stock flag per item."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "pattern": "^SKU-\\d+$"},
                    "product_query": {"type": "string", "minLength": 2},
                },
                "anyOf": [{"required": ["sku"]}, {"required": ["product_query"]}],
                "additionalProperties": False,
            },
            scope=Scope.READ,
        ),
        inventory_check,
    )

    # --- ticket_search (read) ---------------------------------------------
    def ticket_search(args: dict[str, Any]) -> dict[str, Any]:
        tickets = store.search_tickets(
            query=args.get("query"),
            status=args.get("status"),
            priority=args.get("priority"),
            limit=args.get("limit", 20),
        )
        return {"count": len(tickets), "tickets": tickets}

    registry.register(
        ToolDefinition(
            name="ticket_search",
            description=(
                "Search support tickets by free-text query (title/tags), "
                "status, and/or priority."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "in_progress", "resolved", "closed"],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            scope=Scope.READ,
        ),
        ticket_search,
    )

    # --- metric_summary (read) ---------------------------------------------
    def metric_summary(args: dict[str, Any]) -> dict[str, Any]:
        rows = store.metrics(
            metric=args.get("metric"), region=args.get("region"), period=args.get("period")
        )
        summary: dict[str, Any] = {"rows": rows, "count": len(rows)}
        revenue = [r for r in rows if r["metric"] == "revenue"]
        if revenue:
            summary["total_revenue"] = round(sum(r["value"] for r in revenue), 2)
        if args.get("metric") == "revenue" and len(rows) >= 2:
            by_period: dict[str, float] = {}
            for r in rows:
                by_period[r["period"]] = by_period.get(r["period"], 0.0) + r["value"]
            periods = sorted(by_period)
            if len(periods) >= 2:
                first, last = by_period[periods[0]], by_period[periods[-1]]
                if first:
                    summary["period_over_period_change_pct"] = round(
                        (last - first) / first * 100, 2
                    )
        return summary

    registry.register(
        ToolDefinition(
            name="metric_summary",
            description=(
                "Summarize business metrics (revenue, orders_count, "
                "return_rate_pct, avg_order_value), optionally filtered by "
                "region (NA/EU/APAC) and period (e.g. '2026-Q3'). Revenue "
                "queries include totals and period-over-period change."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["revenue", "orders_count", "return_rate_pct", "avg_order_value"],
                    },
                    "region": {"type": "string", "enum": ["NA", "EU", "APAC"]},
                    "period": {"type": "string", "pattern": "^\\d{4}-Q[1-4]$"},
                },
                "additionalProperties": False,
            },
            scope=Scope.READ,
        ),
        metric_summary,
    )

    # --- create_ticket (write) ----------------------------------------------
    def create_ticket(args: dict[str, Any]) -> dict[str, Any]:
        ticket = store.create_ticket(
            title=args["title"],
            priority=args.get("priority", "medium"),
            description=args.get("description"),
        )
        return {"created": True, "ticket": ticket}

    registry.register(
        ToolDefinition(
            name="create_ticket",
            description=(
                "Create a new support ticket. WRITE operation — requires the "
                "'write' permission scope."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 5},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "default": "medium",
                    },
                    "description": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            scope=Scope.WRITE,
            annotations={"destructiveHint": False, "idempotentHint": False},
        ),
        create_ticket,
    )

    return registry
