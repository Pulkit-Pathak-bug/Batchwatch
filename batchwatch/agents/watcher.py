"""Watcher agent: fetches the CDSCO NSQ feed and returns only rows not seen before."""
import os
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
OFFLINE_ENV = "BATCHWATCH_OFFLINE"
TIMEOUT = (3, 10)  # connect, read seconds; with 2 retries a dead network costs about 12 s
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
    """One GET for the month, short timeouts, 2 retries on failure."""
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))
    resp = session.get(URL, headers=HEADERS, timeout=TIMEOUT,
                       params={"month": f"{MONTHS[month - 1]}-{year}", "source": "All", "tab": "nsq"})
    resp.raise_for_status()
    return resp.text  # server declares ISO-8859-1; resp.json() would guess UTF-8 and fail


def cache_path(year: int, month: int) -> Path:
    """Where the last good response for this month is kept."""
    return ALERTS_DIR / f"nsq_{year}-{month:02d}.json"


def cached_feed(year: int, month: int) -> Path | None:
    """This month's cache, else the newest cached month, so a venue in a new month still has data."""
    exact = cache_path(year, month)
    if exact.exists():
        return exact
    found = sorted(ALERTS_DIR.glob("nsq_*.json"))
    return found[-1] if found else None


def find_new_pdfs(conn: sqlite3.Connection, alerts_dir: Path) -> list[Path]:
    """Return PDFs in alerts_dir that are not yet in processed_alerts."""
    raise NotImplementedError


def find_new_alerts(conn: sqlite3.Connection, year: int | None = None, month: int | None = None,
                    fetch: Callable[[int, int], str] = fetch_nsq, info: dict | None = None) -> list[dict]:
    """Fetch a month, parse it, and return (and store) only rows not in the DB yet.

    Falls back to the cached copy if the network is down, BATCHWATCH_OFFLINE=1, or the response
    is not a valid feed (a Wi-Fi login page is parsed before it can touch the cache).
    info['source'] is set to 'live' or 'cache'.
    """
    year, month = (year, month) if year and month else default_period()
    info = {} if info is None else info
    db.log_event(conn, "watcher", "start", f"{MONTHS[month - 1]}-{year}")
    try:
        if os.getenv(OFFLINE_ENV) == "1":
            raise ConnectionError(f"{OFFLINE_ENV}=1, network skipped")
        text = fetch(year, month)
        rows, rejected = parser.parse_nsq_json(text)
        cache_path(year, month).write_text(text, encoding="utf-8")
        info["source"] = "live"
        db.log_event(conn, "watcher", "fetched", f"{len(text)} chars, cached")
    except Exception as exc:
        db.log_event(conn, "watcher", "fetch_failed", repr(exc))
        path = cached_feed(year, month)
        if path is None:
            raise RuntimeError(f"fetch failed and no cached feed in {ALERTS_DIR}") from exc
        rows, rejected = parser.parse_nsq_json(path.read_text(encoding="utf-8"))
        info["source"] = "cache"
        note = "" if path == cache_path(year, month) else f" (no cache for {MONTHS[month - 1]}-{year})"
        db.log_event(conn, "watcher", "used_cache", f"used cached data: {path.name}{note}")
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
