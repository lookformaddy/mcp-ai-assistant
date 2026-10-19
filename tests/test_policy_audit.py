from mcp_ai_assistant.errors import PERMISSION_DENIED
from mcp_ai_assistant.policy import Scope, ToolPolicy

from conftest import rpc


def test_policy_allow_list():
    policy = ToolPolicy(granted_scopes={Scope.READ, Scope.WRITE}, allowed_tools={"order_lookup"})
    policy.check("order_lookup", Scope.READ)
    try:
        policy.check("ticket_search", Scope.READ)
        raise AssertionError("expected PermissionDeniedError")
    except Exception as exc:
        assert getattr(exc, "code", None) == PERMISSION_DENIED


def test_write_tool_denied_for_read_only_caller(read_only_server, audit):
    response = read_only_server.handle(
        rpc("tools/call", {"name": "create_ticket", "arguments": {"title": "A perfectly valid title"}})
    )
    assert response["error"]["code"] == PERMISSION_DENIED
    entries = audit.entries(status="denied")
    assert len(entries) == 1
    assert entries[0].tool == "create_ticket"
    assert entries[0].caller == "test-readonly"


def test_successful_call_is_audited(server, audit):
    response = server.handle(
        rpc("tools/call", {"name": "order_lookup", "arguments": {"order_id": "ORD-1002"}})
    )
    assert "result" in response
    entries = audit.entries(status="ok")
    assert len(entries) == 1
    assert entries[0].tool == "order_lookup"
    assert entries[0].duration_ms is not None


def test_failed_validation_is_audited_as_error(server, audit):
    response = server.handle(
        rpc("tools/call", {"name": "order_lookup", "arguments": {"order_id": "NOT-AN-ORDER"}})
    )
    assert "error" in response
    entries = audit.entries(status="error")
    assert len(entries) == 1
    assert entries[0].tool == "order_lookup"


def test_audit_log_jsonl_persistence(tmp_path):
    from mcp_ai_assistant.audit import AuditEntry, AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(AuditEntry(caller="t", tool="order_lookup", status="ok", arguments={"order_id": "ORD-1001"}))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"order_lookup"' in lines[0]
