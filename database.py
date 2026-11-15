"""SQLite storage for Atelier. The database file is local and never committed."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "atelier.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    website TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    importance TEXT NOT NULL,
    status TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Create the tables and, on first run only, add invented demo data."""
    with get_connection() as connection:
        connection.executescript(SCHEMA)
        has_clients = connection.execute("SELECT 1 FROM clients LIMIT 1").fetchone()
        if has_clients:
            return
        connection.executemany(
            "INSERT INTO clients (name, website) VALUES (?, ?)",
            [("Demo Yachts", "https://example.com"), ("Demo Boutique", "https://example.org")],
        )
        connection.executemany(
            "INSERT INTO tasks (client_id, department, title, description, importance, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "web", "Publish the new homepage",
                 "New hero section and smoother page transitions are ready to go live.",
                 "important", "pending"),
                (2, "brand", "New color palette proposal",
                 "Three palette options based on the current logo.",
                 "important", "pending"),
                (1, "maintenance", "Fixed a broken link in the footer",
                 "The Instagram link pointed to an old profile.",
                 "small", "done"),
            ],
        )
