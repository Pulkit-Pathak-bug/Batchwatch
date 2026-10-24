"""Watcher agent: detects new alert PDFs."""
import sqlite3
from pathlib import Path


def find_new_alerts(conn: sqlite3.Connection, alerts_dir: Path) -> list[Path]:
    """Return PDFs in alerts_dir that are not yet in processed_alerts."""
    raise NotImplementedError
