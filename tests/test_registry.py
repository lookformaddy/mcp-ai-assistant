import pytest

from mcp_ai_assistant.errors import SchemaValidationError, ToolNotFoundError
from mcp_ai_assistant.policy import Scope
from mcp_ai_assistant.registry import ToolDefinition, ToolRegistry


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echo back the message.",
            inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        ),
        lambda args: {"echo": args["message"]},
    )
    return registry


def test_register_and_list():
    registry = _echo_registry()
    tools = registry.list_tools()
    assert [t["name"] for t in tools] == ["echo"]
    assert tools[0]["inputSchema"]["required"] == ["message"]
    assert tools[0]["annotations"]["scope"] == "read"


def test_call_happy_path():
    assert _echo_registry().call("echo", {"message": "hi"}) == {"echo": "hi"}


def test_call_unknown_tool():
    with pytest.raises(ToolNotFoundError):
        _echo_registry().call("missing", {})


def test_call_schema_validation_blocks_bad_args():
    registry = _echo_registry()
    with pytest.raises(SchemaValidationError):
        registry.call("echo", {})  # missing required 'message'
    with pytest.raises(SchemaValidationError):
        registry.call("echo", {"message": 42})  # wrong type
    with pytest.raises(SchemaValidationError):
        registry.call("echo", {"message": "x", "extra": 1})  # additionalProperties


def test_register_rejects_bad_schema():
    registry = ToolRegistry()
    with pytest.raises(Exception):
        registry.register(
            ToolDefinition(
                name="bad",
                description="bad schema",
                inputSchema={"type": "nonsense"},
            ),
            lambda args: {},
        )


def test_builtin_registry_shape(registry):
    names = registry.names()
    assert names == [
        "create_ticket",
        "inventory_check",
        "metric_summary",
        "order_lookup",
        "ticket_search",
    ]
    assert registry.get("create_ticket").scope is Scope.WRITE
    for name in names:
        if name != "create_ticket":
            assert registry.get(name).scope is Scope.READ
