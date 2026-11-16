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

-- One row per in-depth website analysis. The full report is JSON in "data";
-- "scores" repeats the score of each area so two analyses are easy to compare.
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    task_id INTEGER REFERENCES tasks(id),
    website TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    overall_score INTEGER,
    scores TEXT NOT NULL DEFAULT '{}',
    data TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

-- One row each time the Web agent searches for inspiration for a client (step 2a of my method).
CREATE TABLE IF NOT EXISTS inspiration_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    task_id INTEGER REFERENCES tasks(id),
    brief TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

-- A website that could inspire a client's new website. source is 'agent' (found by a search) or 'mine'
-- (a link I added). state is 'looking' while the browser visits it, then 'ready' or 'failed'.
-- verdict is my choice: '' (not decided), 'liked' or 'discarded'. likes says what I like about it.
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    search_id INTEGER REFERENCES inspiration_searches(id),
    source TEXT NOT NULL DEFAULT 'agent',
    url TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    award TEXT NOT NULL DEFAULT '',
    found_at TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '',
    look_at TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'looking',
    verdict TEXT NOT NULL DEFAULT '',
    likes TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The CRM. A clinic I would like as a client: where it is, how to reach it and where we are.
-- stage is its step on the path (the list is in crm.py). next_step and next_step_on say what I do
-- next and on which day. source_url is the page a clinic was imported from, so it never comes in twice.
-- More columns of this table are in NEW_COLUMNS, below: the ones added after the table already existed.
CREATE TABLE IF NOT EXISTS clinics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    instagram TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    batch TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT '',
    treatments TEXT NOT NULL DEFAULT '',
    google_rating REAL,
    google_reviews INTEGER,
    fit INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'to_contact',
    lost_reason TEXT NOT NULL DEFAULT '',
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    next_step TEXT NOT NULL DEFAULT '',
    next_step_on TEXT,
    source_url TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The people I talk to in a clinic. prefers is the channel they like best; decides marks who can say yes.
CREATE TABLE IF NOT EXISTS clinic_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    instagram TEXT NOT NULL DEFAULT '',
    prefers TEXT NOT NULL DEFAULT '',
    decides INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Every contact with a clinic: the day, what it was (kind), where (channel) and what happened.
-- kind 'stage' is a change of stage I made by hand. stage_to is filled when the entry moved the clinic.
CREATE TABLE IF NOT EXISTS clinic_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id INTEGER NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES clinic_people(id) ON DELETE SET NULL,
    happened_on TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    stage_to TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS clinic_history_by_clinic ON clinic_history(clinic_id);
CREATE INDEX IF NOT EXISTS clinic_people_by_clinic ON clinic_people(clinic_id);
"""


# Columns added to a table that already existed on my computer. CREATE TABLE IF NOT EXISTS leaves an
# existing table as it is, so each of these is added once with ALTER TABLE.
# website_check says how I know a clinic's website is really theirs; web_scan keeps what was read on it (JSON).
NEW_COLUMNS = {
    "clinics": {
        "tiktok": "TEXT NOT NULL DEFAULT ''",
        "x": "TEXT NOT NULL DEFAULT ''",
        "threads": "TEXT NOT NULL DEFAULT ''",
        "facebook": "TEXT NOT NULL DEFAULT ''",
        "linkedin": "TEXT NOT NULL DEFAULT ''",
        "website_check": "TEXT NOT NULL DEFAULT ''",
        "web_scan": "TEXT NOT NULL DEFAULT '{}'",
    },
}


def add_new_columns(connection: sqlite3.Connection) -> None:
    for table, columns in NEW_COLUMNS.items():
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def get_connection() -> sqlite3.Connection:
    # The agent sometimes writes from several threads at once: wait for a turn instead of failing.
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Create the tables and, on first run only, add invented demo data.

    The CRM starts empty: its clinics are real, so they never come from this file.
    """
    with get_connection() as connection:
        connection.executescript(SCHEMA)
        add_new_columns(connection)
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
