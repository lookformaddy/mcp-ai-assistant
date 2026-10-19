"""JSON-RPC / MCP error types.

Standard JSON-RPC 2.0 error codes are used where they exist; application
errors live in the reserved server range (-32000..-32099).
"""

from __future__ import annotations

# Standard JSON-RPC 2.0 codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application codes (server range)
TOOL_NOT_FOUND = -32001
SCHEMA_VALIDATION_ERROR = -32002
PERMISSION_DENIED = -32003
RESOURCE_NOT_FOUND = -32004
PROMPT_NOT_FOUND = -32005


class MCPError(Exception):
    """An error that maps cleanly onto a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}

    def to_error_object(self) -> dict:
        obj: dict = {"code": self.code, "message": self.message}
        if self.data:
            obj["data"] = self.data
        return obj


class ToolNotFoundError(MCPError):
    def __init__(self, name: str):
        super().__init__(TOOL_NOT_FOUND, f"Unknown tool: {name}", {"tool": name})


class SchemaValidationError(MCPError):
    def __init__(self, tool: str, detail: str):
        super().__init__(
            SCHEMA_VALIDATION_ERROR,
            f"Arguments for tool '{tool}' failed schema validation: {detail}",
            {"tool": tool, "detail": detail},
        )


class PermissionDeniedError(MCPError):
    def __init__(self, tool: str, reason: str):
        super().__init__(
            PERMISSION_DENIED,
            f"Permission denied for tool '{tool}': {reason}",
            {"tool": tool, "reason": reason},
        )
