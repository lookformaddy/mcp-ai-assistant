import json

from mcp_ai_assistant.errors import METHOD_NOT_FOUND, RESOURCE_NOT_FOUND, TOOL_NOT_FOUND

from conftest import rpc


def test_initialize(server):
    response = server.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}))
    result = response["result"]
    assert result["serverInfo"]["name"] == "mcp-ai-assistant"
    assert set(result["capabilities"]) == {"tools", "resources", "prompts"}


def test_ping(server):
    assert server.handle(rpc("ping"))["result"] == {}


def test_unknown_method(server):
    response = server.handle(rpc("bogus/method"))
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_notification_returns_none(server):
    raw = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert server.handle(raw) is None


def test_tools_list(server):
    result = server.handle(rpc("tools/list"))["result"]
    assert len(result["tools"]) == 5


def test_tools_call_returns_content_and_structured(server):
    response = server.handle(
        rpc("tools/call", {"name": "inventory_check", "arguments": {"sku": "SKU-2006"}})
    )
    result = response["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["inventory"][0]["available"] == 2  # 27 - 25
    assert json.loads(result["content"][0]["text"]) == structured


def test_tools_call_unknown_tool(server):
    response = server.handle(rpc("tools/call", {"name": "nope", "arguments": {}}))
    assert response["error"]["code"] == TOOL_NOT_FOUND


def test_resources_list_and_read(server):
    listed = server.handle(rpc("resources/list"))["result"]["resources"]
    uris = {r["uri"] for r in listed}
    assert "enterprise://inventory/low-stock" in uris
    read = server.handle(
        rpc("resources/read", {"uri": "enterprise://inventory/low-stock"})
    )["result"]
    payload = json.loads(read["contents"][0]["text"])
    skus = {row["sku"] for row in payload}
    assert "SKU-2003" in skus and "SKU-2006" in skus


def test_resource_not_found(server):
    response = server.handle(rpc("resources/read", {"uri": "enterprise://nope"}))
    assert response["error"]["code"] == RESOURCE_NOT_FOUND


def test_prompts_list_and_get(server):
    listed = server.handle(rpc("prompts/list"))["result"]["prompts"]
    assert {p["name"] for p in listed} == {"order_investigation", "weekly_ops_review"}
    got = server.handle(
        rpc("prompts/get", {"name": "order_investigation", "arguments": {"order_id": "ORD-1005"}})
    )["result"]
    text = got["messages"][0]["content"]["text"]
    assert "ORD-1005" in text and "inventory_check" in text


def test_parse_error_response(server):
    response = server.handle("garbage")
    assert response["error"]["code"] == -32700 or response["error"]["code"] == -32600
