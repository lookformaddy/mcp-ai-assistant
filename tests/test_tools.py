from mcp_ai_assistant.tools import build_registry


def _registry(store):
    return build_registry(store)


def test_order_lookup_by_id(store):
    result = _registry(store).call("order_lookup", {"order_id": "ORD-1001"})
    assert result["found"] is True
    order = result["orders"][0]
    assert order["customer"] == "Acme Retail Group"
    assert order["status"] == "delivered"
    assert {i["sku"] for i in order["items"]} == {"SKU-2001", "SKU-2004"}


def test_order_lookup_unknown_id(store):
    result = _registry(store).call("order_lookup", {"order_id": "ORD-9999"})
    assert result["found"] is False
    assert result["orders"] == []


def test_order_lookup_by_customer(store):
    result = _registry(store).call("order_lookup", {"customer": "cedar health"})
    assert result["found"] is True
    assert [o["id"] for o in result["orders"]] == ["ORD-1003"]


def test_inventory_check_computes_availability_and_low_stock(store):
    result = _registry(store).call("inventory_check", {"sku": "SKU-2003"})
    item = result["inventory"][0]
    assert item["available"] == 6  # 18 on hand - 12 reserved
    assert item["low_stock"] is True  # reorder point is 40


def test_inventory_search_by_product(store):
    result = _registry(store).call("inventory_check", {"product_query": "scanner"})
    assert result["found"] is True
    assert result["inventory"][0]["sku"] == "SKU-2001"


def test_ticket_search_filters(store):
    registry = _registry(store)
    open_critical = registry.call("ticket_search", {"status": "open", "priority": "critical"})
    assert open_critical["count"] == 1
    assert open_critical["tickets"][0]["id"] == "TKT-5003"
    hardware = registry.call("ticket_search", {"query": "hardware"})
    assert hardware["count"] >= 2


def test_metric_summary_revenue_totals_and_pop(store):
    result = _registry(store).call("metric_summary", {"metric": "revenue"})
    # Q2 total 3,917,600 + Q3 total 4,190,500
    assert result["total_revenue"] == 8108100.0
    assert result["period_over_period_change_pct"] == 6.97


def test_metric_summary_filters(store):
    result = _registry(store).call(
        "metric_summary", {"metric": "revenue", "region": "EU", "period": "2026-Q3"}
    )
    assert result["count"] == 1
    assert result["rows"][0]["value"] == 1302750.0


def test_create_ticket_persists(store):
    registry = _registry(store)
    created = registry.call("create_ticket", {"title": "Dock door 4 sensor offline", "priority": "high"})
    assert created["created"] is True
    ticket = created["ticket"]
    assert ticket["status"] == "open"
    found = registry.call("ticket_search", {"query": "dock door"})
    assert found["count"] == 1
    assert found["tickets"][0]["id"] == ticket["id"]
