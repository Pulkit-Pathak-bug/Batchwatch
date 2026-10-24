"""SQLite storage: processed_alerts, alert_rows, matches, audit_log."""
import sqlite3
import sys
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
    drug_name TEXT NOT NULL, brand_name TEXT, batch_no TEXT NOT NULL,
    manufacturer TEXT, mfg_date TEXT, exp_date TEXT, reason TEXT,
    source TEXT, lab_state TEXT, report_month TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    alert_row_id INTEGER NOT NULL REFERENCES alert_rows(id),
    stock_drug TEXT, stock_batch TEXT NOT NULL, stock_manufacturer TEXT, exp_date TEXT,
    quantity TEXT, shelf_location TEXT,
    status TEXT CHECK (status IN ('confirmed', 'uncertain')),
    score REAL, reason TEXT, alert_text TEXT, return_text TEXT,
    decision TEXT DEFAULT 'pending', decided_by TEXT, decided_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_match ON matches (alert_row_id, stock_batch);
CREATE TABLE IF NOT EXISTS approvers (
    user_id INTEGER PRIMARY KEY,
    granted_by INTEGER,
    granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
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

ADDED_COLUMNS = {
    "alert_rows": {"brand_name": "TEXT"},
    "matches": {"stock_manufacturer": "TEXT", "exp_date": "TEXT", "quantity": "TEXT",
                "shelf_location": "TEXT", "score": "REAL", "reason": "TEXT", "alert_text": "TEXT",
                "return_text": "TEXT", "decided_by": "TEXT", "decided_at": "TEXT"},
}

ROW_COLUMNS = ("drug_name", "brand_name", "batch_no", "manufacturer", "mfg_date", "exp_date",
               "reason", "source", "lab_state", "report_month")


def init_db(path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open the database, create the tables, and add columns that older files lack."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()
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


def load_alert_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every stored alert row, including its id."""
    return [dict(r) for r in conn.execute("SELECT * FROM alert_rows ORDER BY id")]


def save_match(conn: sqlite3.Connection, match: dict) -> tuple[int, bool]:
    """Store a matcher result plus drafts. Returns (match id, True if newly created).

    The (alert_row_id, stock_batch) unique index makes a repeat insert a no-op.
    """
    stock, alert = match["stock"], match["alert"]
    cur = conn.execute(
        "INSERT OR IGNORE INTO matches (alert_row_id, stock_drug, stock_batch, stock_manufacturer, "
        "exp_date, quantity, shelf_location, status, score, reason, alert_text, return_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (alert["id"], stock.get("drug_name"), stock.get("batch_no"), stock.get("manufacturer"),
         stock.get("exp_date"), stock.get("quantity"), stock.get("shelf_location"), match["status"],
         match["score"], match["reason"], match.get("alert_text"), match.get("return_text")),
    )
    created = cur.rowcount == 1
    conn.commit()
    row = conn.execute("SELECT id FROM matches WHERE alert_row_id = ? AND stock_batch = ?",
                       (alert["id"], stock.get("batch_no"))).fetchone()
    return row["id"], created


def get_match(conn: sqlite3.Connection, match_id: int) -> dict | None:
    """One match with its alert row nested under 'alert', or None."""
    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if row is None:
        return None
    match = dict(row)
    match["alert"] = dict(conn.execute("SELECT * FROM alert_rows WHERE id = ?",
                                       (row["alert_row_id"],)).fetchone())
    return match


def pending_matches(conn: sqlite3.Connection) -> list[dict]:
    """Undecided matches, confirmed first, then by score."""
    ids = conn.execute("SELECT id FROM matches WHERE decision = 'pending' "
                       "ORDER BY (status = 'confirmed') DESC, score DESC, id").fetchall()
    return [get_match(conn, r["id"]) for r in ids]


def decide_match(conn: sqlite3.Connection, match_id: int, decision: str, user_id: int) -> bool:
    """Atomically move a match from pending to approved/rejected. False if already decided."""
    cur = conn.execute("UPDATE matches SET decision = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP "
                       "WHERE id = ? AND decision = 'pending'", (decision, str(user_id), match_id))
    conn.commit()
    return cur.rowcount == 1


def is_granted(conn: sqlite3.Connection, user_id: int) -> bool:
    """True if user_id is an active approver granted through the bot."""
    return conn.execute("SELECT 1 FROM approvers WHERE user_id = ? AND revoked_at IS NULL",
                        (user_id,)).fetchone() is not None


def grant_approver(conn: sqlite3.Connection, user_id: int, granted_by: int) -> bool:
    """Make user_id an approver (re-grants after a revoke). False if already active."""
    if is_granted(conn, user_id):
        return False
    conn.execute("INSERT INTO approvers (user_id, granted_by) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE "
                 "SET granted_by = excluded.granted_by, granted_at = CURRENT_TIMESTAMP, revoked_at = NULL",
                 (user_id, granted_by))
    conn.commit()
    return True


def revoke_approver(conn: sqlite3.Connection, user_id: int) -> bool:
    """Soft-revoke (the row stays for history). False if user_id was not an active approver."""
    cur = conn.execute("UPDATE approvers SET revoked_at = CURRENT_TIMESTAMP "
                       "WHERE user_id = ? AND revoked_at IS NULL", (user_id,))
    conn.commit()
    return cur.rowcount == 1


def list_approvers(conn: sqlite3.Connection) -> list[dict]:
    """Active granted approvers, oldest grant first."""
    return [dict(r) for r in conn.execute("SELECT user_id, granted_by, granted_at FROM approvers "
                                          "WHERE revoked_at IS NULL ORDER BY granted_at, user_id")]


def recent_log(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Newest audit_log entries first."""
    return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


def log_event(conn: sqlite3.Connection | None, actor: str, action: str, detail: str) -> None:
    """Append to audit_log (if conn is given) and echo to the console. No update or delete exists."""
    if conn is not None:
        conn.execute("INSERT INTO audit_log (actor, action, detail) VALUES (?, ?, ?)",
                     (actor, action, detail))
        conn.commit()
    encoding = sys.stdout.encoding or "utf-8"
    print(f"[{actor}] {action}: {detail}".encode(encoding, "replace").decode(encoding))
