"""SQLite storage: processed_alerts, alert_rows, matches, audit_log."""
import sqlite3


def init_db(path: str) -> sqlite3.Connection:
    """Open the database and create the four tables if missing."""
    raise NotImplementedError


def mark_alert_processed(conn: sqlite3.Connection, filename: str) -> None:
    """Record an alert PDF as processed."""
    raise NotImplementedError


def save_alert_rows(conn: sqlite3.Connection, filename: str, rows: list[dict]) -> None:
    """Store parsed alert rows for a PDF."""
    raise NotImplementedError


def save_match(conn: sqlite3.Connection, match: dict) -> int:
    """Store a match and return its id."""
    raise NotImplementedError


def log_event(conn: sqlite3.Connection, actor: str, action: str, detail: str) -> None:
    """Append to audit_log. This is the only write path; no update or delete."""
    raise NotImplementedError
