import pytest
from fastapi.testclient import TestClient

from mcp_ai_assistant.server import create_app


@pytest.fixture()
def client(server):
    return TestClient(create_app(server))


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_rpc_round_trip(client):
    resp = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ticket_search", "arguments": {"status": "open"}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["structuredContent"]["count"] == 2


def test_tools_endpoint(client):
    resp = client.get("/tools")
    assert resp.status_code == 200
    assert len(resp.json()["tools"]) == 5


def test_audit_endpoint_reflects_calls(client):
    client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "order_lookup", "arguments": {"order_id": "ORD-1001"}},
        },
    )
    resp = client.get("/audit")
    assert resp.json()["count"] == 1
    assert resp.json()["entries"][0]["tool"] == "order_lookup"


def test_rpc_bad_json_body(client):
    resp = client.post("/rpc", content="{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32700
