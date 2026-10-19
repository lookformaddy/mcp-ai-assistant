"""Synthetic enterprise data store.

Loads JSON fixtures (orders, order items, inventory, support tickets,
business metrics — all invented) into a SQLite database. SQLite gives the
tools real SQL querying while staying dependency-free and file-portable:
pass ``db_path`` for a persistent database or omit it for in-memory.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


def _synchronized(method):
    """Serialize access to the shared SQLite connection.

    HTTP transports serve requests from worker threads, and a sqlite3
    connection is not safe for unsynchronized cross-thread use — so all
    public store operations take the store's re-entrant lock.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def default_fixtures_path() -> Path:
    """Locate ``data/fixtures.json`` relative to this source tree."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "fixtures.json"
        if candidate.exists():
            return candidate
    return here.parent / "fixtures.json"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    status TEXT NOT NULL,
    total REAL NOT NULL,
    currency TEXT NOT NULL,
    placed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    on_hand INTEGER NOT NULL,
    reserved INTEGER NOT NULL,
    reorder_point INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    assignee TEXT,
    created_at TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS metrics (
    period TEXT NOT NULL,
    region TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (period, region, metric)
);
"""


class DataStore:
    def __init__(self, db_path: str | Path | None = None):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(db_path) if db_path else ":memory:", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # -- loading ---------------------------------------------------------
    @classmethod
    def from_fixtures(
        cls, fixtures_path: str | Path | None = None, db_path: str | Path | None = None
    ) -> "DataStore":
        path = Path(fixtures_path) if fixtures_path else default_fixtures_path()
        with path.open(encoding="utf-8") as fh:
            fixtures = json.load(fh)
        store = cls(db_path)
        store.load(fixtures)
        return store

    @classmethod
    def from_dict(cls, fixtures: dict[str, Any]) -> "DataStore":
        store = cls()
        store.load(fixtures)
        return store

    @_synchronized
    def load(self, fixtures: dict[str, Any]) -> None:
        cur = self._conn.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO orders VALUES (:id,:customer,:status,:total,:currency,:placed_at)",
            fixtures.get("orders", []),
        )
        cur.executemany(
            "INSERT INTO order_items VALUES (:order_id,:sku,:quantity,:unit_price)",
            fixtures.get("order_items", []),
        )
        cur.executemany(
            "INSERT OR REPLACE INTO inventory VALUES (:sku,:product,:warehouse,:on_hand,:reserved,:reorder_point)",
            fixtures.get("inventory", []),
        )
        for ticket in fixtures.get("tickets", []):
            cur.execute(
                "INSERT OR REPLACE INTO tickets VALUES (:id,:title,:status,:priority,:assignee,:created_at,:tags)",
                {**ticket, "tags": json.dumps(ticket.get("tags", []))},
            )
        cur.executemany(
            "INSERT OR REPLACE INTO metrics VALUES (:period,:region,:metric,:value)",
            fixtures.get("metrics", []),
        )
        self._conn.commit()

    # -- queries used by tools ------------------------------------------
    @_synchronized
    def get_order(self, order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        order = dict(row)
        items = self._conn.execute(
            "SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchall()
        order["items"] = [dict(i) for i in items]
        return order

    @_synchronized
    def orders_for_customer(self, customer: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id FROM orders WHERE lower(customer) LIKE lower(?) ORDER BY placed_at DESC",
            (f"%{customer}%",),
        ).fetchall()
        return [self.get_order(r["id"]) for r in rows]

    @_synchronized
    def get_inventory(self, sku: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM inventory WHERE sku = ?", (sku,)).fetchone()
        return dict(row) if row else None

    @_synchronized
    def search_inventory(self, query: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM inventory WHERE lower(product) LIKE lower(?) OR sku LIKE ? ORDER BY sku",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def low_stock(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM inventory WHERE (on_hand - reserved) <= reorder_point ORDER BY sku"
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def search_tickets(
        self,
        query: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tickets WHERE 1=1"
        params: list[Any] = []
        if query:
            sql += " AND (lower(title) LIKE lower(?) OR lower(tags) LIKE lower(?))"
            params += [f"%{query}%", f"%{query}%"]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        tickets = []
        for row in rows:
            ticket = dict(row)
            ticket["tags"] = json.loads(ticket["tags"])
            tickets.append(ticket)
        return tickets

    @_synchronized
    def create_ticket(self, title: str, priority: str, description: str | None = None) -> dict[str, Any]:
        row = self._conn.execute("SELECT COALESCE(MAX(CAST(substr(id, 5) AS INTEGER)), 5000) AS m FROM tickets").fetchone()
        new_id = f"TKT-{row['m'] + 1}"
        from datetime import datetime, timezone

        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            "INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
            (new_id, title, "open", priority, "support-team-a", created, json.dumps(["user-created"])),
        )
        self._conn.commit()
        ticket = {
            "id": new_id,
            "title": title,
            "status": "open",
            "priority": priority,
            "assignee": "support-team-a",
            "created_at": created,
            "tags": ["user-created"],
        }
        if description:
            ticket["description"] = description
        return ticket

    @_synchronized
    def metrics(
        self, metric: str | None = None, region: str | None = None, period: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM metrics WHERE 1=1"
        params: list[Any] = []
        if metric:
            sql += " AND metric = ?"
            params.append(metric)
        if region:
            sql += " AND region = ?"
            params.append(region)
        if period:
            sql += " AND period = ?"
            params.append(period)
        sql += " ORDER BY period, region, metric"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def recent_orders(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id FROM orders ORDER BY placed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.get_order(r["id"]) for r in rows]
