"""Orchestrator: watch -> match -> act, sharing one state dict. Parsing happens inside the watcher."""
import sqlite3
from pathlib import Path
from typing import Callable

from batchwatch import db
from batchwatch.agents import actor, matcher, watcher

STOCK_CSV = Path(__file__).resolve().parent / "data" / "stock.csv"

STOP_RULES = [
    (lambda s: s["alert_rows"] is not None and not s["alert_rows"], "no alert rows stored"),
    (lambda s: s["stock_rows"] is not None and not s["stock_rows"], "stock list is empty"),
    (lambda s: s["matches"] is not None and not s["matches"], "no stock row matches any alert"),
]


def should_stop(state: dict) -> bool:
    """Stop rules; records the first rule that fires in state['stop_reason']."""
    for rule, reason in STOP_RULES:
        if rule(state):
            state["stop_reason"] = reason
            return True
    return False


def run_pipeline(conn: sqlite3.Connection | None = None, stock_path: Path = STOCK_CSV,
                 fetch: Callable[[int, int], str] = watcher.fetch_nsq,
                 year: int | None = None, month: int | None = None) -> dict:
    """Run watch -> match -> act once and return the final state.

    Stock is matched against every stored alert row, not just new ones; the unique index
    on matches stops anything already queued or decided from being queued again.
    """
    own_conn = conn is None
    conn = db.init_db() if own_conn else conn
    state = {"new_alert_rows": [], "alert_rows": None, "stock_rows": None, "matches": None,
             "counts": {"confirmed": 0, "uncertain": 0}, "new_match_ids": [], "pending_total": 0,
             "stop_reason": None, "data_source": None}
    try:
        db.log_event(conn, "orchestrator", "pipeline_start", f"stock={stock_path.name}")
        info: dict = {}
        state["new_alert_rows"] = watcher.find_new_alerts(conn, year, month, fetch=fetch, info=info)
        state["data_source"] = info.get("source")
        state["alert_rows"] = db.load_alert_rows(conn)
        state["stock_rows"] = matcher.load_stock_csv(stock_path)

        if not should_stop(state):
            results = matcher.match_stock(state["stock_rows"], state["alert_rows"])
            state["matches"] = [r for r in results if r["status"] != "none"]
            for r in state["matches"]:
                state["counts"][r["status"]] += 1
            db.log_event(conn, "orchestrator", "matched",
                         f"{state['counts']['confirmed']} confirmed, {state['counts']['uncertain']} uncertain")

        if not should_stop(state):
            for r in state["matches"]:
                r["alert_text"] = actor.draft_alert(r)
                r["return_text"] = actor.draft_return_request(r)
                match_id, created = db.save_match(conn, r)
                if created:
                    state["new_match_ids"].append(match_id)
            db.log_event(conn, "orchestrator", "drafted", f"{len(state['new_match_ids'])} new matches queued")

        if state["stop_reason"]:
            db.log_event(conn, "orchestrator", "pipeline_stopped", state["stop_reason"])
        state["pending_total"] = len(db.pending_matches(conn))
        db.log_event(conn, "orchestrator", "pipeline_done", f"{state['pending_total']} pending approval")
        return state
    finally:
        if own_conn:
            conn.close()
