"""Tool registry: named tools with JSON-Schema contracts.

Tools are registered with a JSON Schema for their arguments (and optionally
their result). Every call is validated against the argument schema with
``jsonschema`` before the handler runs — malformed calls never reach
business logic.
"""

from __future__ import annotations

from typing import Any, Callable

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

from .errors import SchemaValidationError, ToolNotFoundError
from .policy import Scope

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolDefinition(BaseModel):
    """The public contract of a tool (what ``tools/list`` returns)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    scope: Scope = Scope.READ
    annotations: dict[str, Any] = Field(default_factory=dict)

    def to_mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"scope": self.scope.value, **self.annotations},
        }


class ToolRegistry:
    def __init__(self):
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        # Fail fast on a broken schema at registration time, not at call time.
        jsonschema.Draft202012Validator.check_schema(definition.input_schema)
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def names(self) -> list[str]:
        return sorted(self._definitions)

    def list_tools(self) -> list[dict[str, Any]]:
        return [self._definitions[n].to_mcp() for n in self.names()]

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        definition = self.get(name)
        arguments = arguments or {}
        try:
            jsonschema.validate(arguments, definition.input_schema)
        except jsonschema.ValidationError as exc:
            raise SchemaValidationError(name, exc.message) from exc
        return self._handlers[name](arguments)
