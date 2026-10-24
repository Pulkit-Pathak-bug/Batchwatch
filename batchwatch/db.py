"""SQLite storage: processed_alerts, alert_rows, matches, audit_log."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "batchwatch.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_alerts (
    filename TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alert_rows (
    id INTEGER PRIMARY KEY,
    row_key TEXT NOT NULL UNIQUE,
    drug_name TEXT NOT NULL, batch_no TEXT NOT NULL,
    manufacturer TEXT, mfg_date TEXT, exp_date TEXT, reason TEXT,
    source TEXT, lab_state TEXT, report_month TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    alert_row_id INTEGER REFERENCES alert_rows(id),
    stock_drug TEXT, stock_batch TEXT,
    status TEXT CHECK (status IN ('confirmed', 'uncertain')),
    decision TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    actor TEXT, action TEXT, detail TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

ROW_COLUMNS = ("drug_name", "batch_no", "manufacturer", "mfg_date", "exp_date",
               "reason", "source", "lab_state", "report_month")


def init_db(path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open the database and create the four tables if missing."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def row_key(row: dict) -> str:
    """Dedupe key: lower-cased drug name + batch number, whitespace collapsed."""
    return "|".join(" ".join(str(row.get(k, "")).split()).lower()
                    for k in ("drug_name", "batch_no"))


def known_keys(conn: sqlite3.Connection) -> set[str]:
    """Return the row_key of every alert row already stored."""
    return {r[0] for r in conn.execute("SELECT row_key FROM alert_rows")}


def mark_alert_processed(conn: sqlite3.Connection, filename: str) -> None:
    """Record an alert PDF as processed."""
    conn.execute("INSERT OR IGNORE INTO processed_alerts (filename) VALUES (?)", (filename,))
    conn.commit()


def save_alert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert rows, silently skipping known keys. Return how many were new."""
    cols = ", ".join(ROW_COLUMNS)
    marks = ", ".join("?" * (len(ROW_COLUMNS) + 1))
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO alert_rows (row_key, {cols}) VALUES ({marks})",
        [(row_key(r), *(r.get(c, "") for c in ROW_COLUMNS)) for r in rows],
    )
    conn.commit()
    return conn.total_changes - before


def save_match(conn: sqlite3.Connection, match: dict) -> int:
    """Store a match and return its id."""
    raise NotImplementedError


def log_event(conn: sqlite3.Connection | None, actor: str, action: str, detail: str) -> None:
    """Append to audit_log (print if conn is None). No update or delete exists."""
    if conn is None:
        print(f"[{actor}] {action}: {detail}")
        return
    conn.execute("INSERT INTO audit_log (actor, action, detail) VALUES (?, ?, ?)",
                 (actor, action, detail))
    conn.commit()
    print(f"[{actor}] {action}: {detail}")
