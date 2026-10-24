"""Watcher agent: fetches the CDSCO NSQ feed and returns only rows not seen before."""
import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from batchwatch import db
from batchwatch.agents import parser

ALERTS_DIR = Path(__file__).resolve().parents[1] / "data" / "alerts"
URL = "https://cdscoonline.gov.in/CDSCO/filteredNsqDrugTable"
HEADERS = {
    "Referer": "https://cdscoonline.gov.in/CDSCO/viewPublicNSQDrug",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def default_period(today: date | None = None) -> tuple[int, int]:
    """Latest complete month (CDSCO publishes after month end)."""
    today = today or date.today()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def fetch_nsq(year: int, month: int) -> str:
    """One GET for the month, 10s connect/30s read timeout, 2 retries on failure."""
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))
    resp = session.get(URL, headers=HEADERS, timeout=(10, 30),
                       params={"month": f"{MONTHS[month - 1]}-{year}", "source": "All", "tab": "nsq"})
    resp.raise_for_status()
    return resp.text  # server declares ISO-8859-1; resp.json() would guess UTF-8 and fail


def cache_path(year: int, month: int) -> Path:
    """Where the last good response for this month is kept."""
    return ALERTS_DIR / f"nsq_{year}-{month:02d}.json"


def find_new_pdfs(conn: sqlite3.Connection, alerts_dir: Path) -> list[Path]:
    """Return PDFs in alerts_dir that are not yet in processed_alerts."""
    raise NotImplementedError


def find_new_alerts(conn: sqlite3.Connection, year: int | None = None, month: int | None = None,
                    fetch: Callable[[int, int], str] = fetch_nsq) -> list[dict]:
    """Fetch a month, parse it, and return (and store) only rows not in the DB yet."""
    year, month = (year, month) if year and month else default_period()
    db.log_event(conn, "watcher", "start", f"{MONTHS[month - 1]}-{year}")
    try:
        text = fetch(year, month)
        cache_path(year, month).write_text(text, encoding="utf-8")
        db.log_event(conn, "watcher", "fetched", f"{len(text)} chars, cached")
    except Exception as exc:
        db.log_event(conn, "watcher", "fetch_failed", repr(exc))
        path = cache_path(year, month)
        if not path.exists():
            raise RuntimeError(f"fetch failed and no cache at {path}") from exc
        text = path.read_text(encoding="utf-8")
        db.log_event(conn, "watcher", "used_cache", path.name)

    rows, rejected = parser.parse_nsq_json(text)
    db.log_event(conn, "watcher", "parsed", f"{len(rows)} valid, {len(rejected)} rejected")

    known = db.known_keys(conn)
    new = []
    for row in rows:
        key = db.row_key(row)
        if key not in known:
            known.add(key)  # also collapses duplicates inside one feed
            new.append(row)
    db.save_alert_rows(conn, new)
    db.log_event(conn, "watcher", "new_rows", f"{len(new)} new of {len(rows)}")
    return new


if __name__ == "__main__":
    connection = db.init_db()
    found = find_new_alerts(connection, 2026, 9)
    print(f"\n{len(found)} new rows")
    for item in found[:5]:
        print(item)
