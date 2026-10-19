"""JSON-RPC 2.0 message models for the MCP-style protocol.

The wire format mirrors the Model Context Protocol: JSON-RPC 2.0 requests,
responses, and notifications, with MCP method names (``tools/list``,
``tools/call``, ``resources/list``, ``resources/read``, ``prompts/list``,
``prompts/get``, ``initialize``, ``ping``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import INVALID_REQUEST, MCPError


class JSONRPCRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_notification(self) -> bool:
        return self.id is None


class JSONRPCErrorObject(BaseModel):
    code: int
    message: str
    data: dict[str, Any] | None = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    result: Any = None
    error: JSONRPCErrorObject | None = None

    def to_wire(self) -> dict:
        """Serialize, omitting the unused result/error branch (JSON-RPC rules)."""
        wire: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            wire["error"] = self.error.model_dump(exclude_none=True)
        else:
            wire["result"] = self.result
        return wire


def success_response(request_id: Any, result: Any) -> dict:
    return JSONRPCResponse(id=request_id, result=result).to_wire()


def error_response(request_id: Any, error: MCPError) -> dict:
    return JSONRPCResponse(
        id=request_id, error=JSONRPCErrorObject(**error.to_error_object())
    ).to_wire()


def parse_request(raw: Any) -> JSONRPCRequest:
    """Validate and parse a raw decoded JSON payload into a request."""
    if not isinstance(raw, dict):
        raise MCPError(INVALID_REQUEST, "Request must be a JSON object")
    if raw.get("jsonrpc") != "2.0":
        raise MCPError(INVALID_REQUEST, "Missing or invalid 'jsonrpc' version field")
    if "method" not in raw or not isinstance(raw["method"], str):
        raise MCPError(INVALID_REQUEST, "Missing or invalid 'method' field")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise MCPError(INVALID_REQUEST, "'params' must be an object")
    return JSONRPCRequest(id=raw.get("id"), method=raw["method"], params=params)
