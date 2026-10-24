"""Streamlit read-only dashboard.
Run:  streamlit run batchwatch/dashboard.py --server.address localhost
(without the flag Streamlit listens on every network interface, and this page has no login)
"""
import sqlite3
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchwatch import db  # noqa: E402

st.set_page_config(page_title="BatchWatch", layout="wide")


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT on a read-only connection: SQLite itself refuses any write."""
    conn = sqlite3.connect(f"{db.DB_PATH.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def one(sql: str) -> int:
    """First column of the first row of a COUNT query."""
    return list(query(sql)[0].values())[0]


st.sidebar.title("BatchWatch")
st.sidebar.info("Viewer role: read-only, approvals happen in Telegram")
st.sidebar.button("Refresh data")
st.title("BatchWatch: NSQ stock check")

if not db.DB_PATH.exists():
    st.warning("No database yet. Send /run to the Telegram bot first.")
    st.stop()

try:
    alerts = one("SELECT (SELECT COUNT(DISTINCT NULLIF(report_month, '')) FROM alert_rows)"
                 " + (SELECT COUNT(*) FROM processed_alerts)")
    batches = one("SELECT COUNT(*) FROM alert_rows")
    by_status = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM matches GROUP BY status")}
    by_decision = {r["decision"]: r["n"] for r in query("SELECT decision, COUNT(*) n FROM matches GROUP BY decision")}
    flagged = query(
        "SELECT m.id, m.status, m.decision, m.stock_drug AS drug, m.stock_batch AS batch,"
        " m.shelf_location AS shelf, m.quantity AS qty, ROUND(m.score) AS score, m.reason,"
        " a.report_month AS alert_month FROM matches m JOIN alert_rows a ON a.id = m.alert_row_id"
        " ORDER BY (m.status = 'confirmed') DESC, m.score DESC, m.id")
    agents = [r["actor"] for r in query("SELECT DISTINCT actor FROM audit_log ORDER BY actor")]
except sqlite3.OperationalError as exc:
    st.error(f"Database is missing tables or columns ({exc}). Restart the bot or run the pipeline once.")
    st.stop()

cols = st.columns(4)
cols[0].metric("Alerts processed", alerts, help="Monthly NSQ feeds plus PDF alerts seen so far")
cols[1].metric("Batches extracted", batches, help="Rows parsed from alerts and stored")
cols[2].metric("Confirmed", by_status.get("confirmed", 0))
cols[3].metric("Uncertain", by_status.get("uncertain", 0), help="Needs a human check")
st.caption(f"Decisions: {by_decision.get('pending', 0)} pending, {by_decision.get('approved', 0)} approved, "
           f"{by_decision.get('rejected', 0)} rejected")

st.subheader("Flagged stock")
if flagged:
    st.dataframe(flagged, hide_index=True, column_config={"reason": st.column_config.TextColumn(width="large")})
else:
    st.info("No stock matches an alert yet.")

st.subheader("Audit log")
chosen = st.multiselect("Filter by agent (empty = all)", agents)
where = f" WHERE actor IN ({','.join('?' * len(chosen))})" if chosen else ""
log = query(f"SELECT ts, actor AS agent, action, detail FROM audit_log{where} ORDER BY id DESC LIMIT 200",
            tuple(chosen))
st.dataframe(log, hide_index=True)
st.caption("Newest 200 entries, times in UTC. The log is append-only.")
