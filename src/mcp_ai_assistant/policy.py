"""Permission scopes and allow-list policy for tool execution.

Two independent gates, both enforced before a tool runs:

1. **Allow-list** — if an explicit allow-list is configured, a tool must be
   on it. ``None`` means "all registered tools are allowed".
2. **Scope** — every tool declares a scope: ``read`` (safe queries) or
   ``write`` (mutates state). A caller must hold the tool's scope. Granting
   ``write`` implies ``read`` is normally granted too, but they are tracked
   separately so read-only callers can be issued with least privilege.
"""

from __future__ import annotations

from enum import Enum

from .errors import PermissionDeniedError


class Scope(str, Enum):
    READ = "read"
    WRITE = "write"


class ToolPolicy:
    def __init__(
        self,
        granted_scopes: set[Scope] | None = None,
        allowed_tools: set[str] | None = None,
    ):
        # Default posture: read-only. Writes must be opted into.
        self.granted_scopes: set[Scope] = (
            granted_scopes if granted_scopes is not None else {Scope.READ}
        )
        self.allowed_tools = allowed_tools

    def check(self, tool_name: str, tool_scope: Scope) -> None:
        """Raise PermissionDeniedError if the call is not permitted."""
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise PermissionDeniedError(tool_name, "tool is not on the allow-list")
        if tool_scope not in self.granted_scopes:
            raise PermissionDeniedError(
                tool_name,
                f"requires '{tool_scope.value}' scope; caller holds "
                f"{sorted(s.value for s in self.granted_scopes) or 'no scopes'}",
            )

    def permits(self, tool_name: str, tool_scope: Scope) -> bool:
        try:
            self.check(tool_name, tool_scope)
            return True
        except PermissionDeniedError:
            return False
