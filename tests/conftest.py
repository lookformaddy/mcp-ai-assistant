import pytest

from mcp_ai_assistant.audit import AuditLog
from mcp_ai_assistant.data import DataStore
from mcp_ai_assistant.policy import Scope, ToolPolicy
from mcp_ai_assistant.server import MCPServer
from mcp_ai_assistant.tools import build_registry


@pytest.fixture()
def store() -> DataStore:
    return DataStore.from_fixtures()


@pytest.fixture()
def registry(store):
    return build_registry(store)


@pytest.fixture()
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture()
def server(store, registry, audit) -> MCPServer:
    return MCPServer(
        registry=registry,
        store=store,
        policy=ToolPolicy(granted_scopes={Scope.READ, Scope.WRITE}),
        audit_log=audit,
        caller="test",
    )


@pytest.fixture()
def read_only_server(store, registry, audit) -> MCPServer:
    return MCPServer(
        registry=registry,
        store=store,
        policy=ToolPolicy(granted_scopes={Scope.READ}),
        audit_log=audit,
        caller="test-readonly",
    )


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
