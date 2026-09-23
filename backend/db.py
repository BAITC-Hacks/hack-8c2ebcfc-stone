"""SQLite helpers for local Stone data."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from backend.config import settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    date TEXT NOT NULL,
    comment TEXT DEFAULT ''
);
"""


def get_connection(database_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection and return rows as dictionaries."""

    path = Path(database_path or settings.database_path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(database_path: str | Path | None = None) -> None:
    """Create required tables if they do not exist yet."""

    with get_connection(database_path) as connection:
        connection.execute(SCHEMA_SQL)
        connection.commit()


def list_transactions(database_path: str | Path | None = None) -> list[dict]:
    """Return transactions ordered from newest to oldest."""

    init_db(database_path)
    with get_connection(database_path) as connection:
        rows: Iterable[sqlite3.Row] = connection.execute(
            "SELECT id, amount, category, date, comment FROM transactions ORDER BY id DESC"
        )
        return [dict(row) for row in rows]


def add_transaction(
    amount: float,
    category: str,
    date: str,
    comment: str = "",
    database_path: str | Path | None = None,
) -> dict:
    """Insert one transaction and return the stored row."""

    init_db(database_path)
    with get_connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO transactions (amount, category, date, comment)
            VALUES (?, ?, ?, ?)
            """,
            (amount, category, date, comment),
        )
        connection.commit()
        row = connection.execute(
            "SELECT id, amount, category, date, comment FROM transactions WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return dict(row)
