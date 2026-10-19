"""Audit log: an append-only record of every tool call.

Every ``tools/call`` the server processes — allowed, denied, or failed —
produces one entry. Entries are kept in memory and can optionally be
appended to a JSONL file for durable, queryable history.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditEntry(BaseModel):
    timestamp: str = Field(default_factory=_utcnow_iso)
    caller: str
    tool: str
    scope: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str  # "ok" | "denied" | "error"
    duration_ms: float | None = None
    error: str | None = None
    result_summary: str | None = None


class AuditLog:
    """Thread-safe in-memory audit log with optional JSONL persistence."""

    def __init__(self, path: str | Path | None = None):
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()
        self._path = Path(path) if path else None

    def record(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            self._entries.append(entry)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry.model_dump()) + "\n")
        return entry

    def entries(self, tool: str | None = None, status: str | None = None) -> list[AuditEntry]:
        with self._lock:
            snapshot = list(self._entries)
        if tool is not None:
            snapshot = [e for e in snapshot if e.tool == tool]
        if status is not None:
            snapshot = [e for e in snapshot if e.status == status]
        return snapshot

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
