import pytest

from mcp_ai_assistant.errors import INVALID_REQUEST, MCPError
from mcp_ai_assistant.protocol import error_response, parse_request, success_response


def test_parse_valid_request():
    req = parse_request({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
    assert req.id == 7
    assert req.method == "tools/list"
    assert not req.is_notification


def test_parse_notification_has_no_id():
    req = parse_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert req.is_notification


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-dict",
        {"id": 1, "method": "ping"},  # missing jsonrpc
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},  # wrong version
        {"jsonrpc": "2.0", "id": 1},  # missing method
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2]},  # params not object
    ],
)
def test_parse_invalid_requests_raise(raw):
    with pytest.raises(MCPError) as excinfo:
        parse_request(raw)
    assert excinfo.value.code == INVALID_REQUEST


def test_success_response_shape():
    wire = success_response(3, {"ok": True})
    assert wire == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    assert "error" not in wire


def test_error_response_shape():
    wire = error_response(4, MCPError(-32601, "nope", {"x": 1}))
    assert wire["id"] == 4
    assert wire["error"] == {"code": -32601, "message": "nope", "data": {"x": 1}}
    assert "result" not in wire
