import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from batchwatch import db
from tests.fixtures import STOCK, seed_match

DASHBOARD = Path(__file__).resolve().parents[1] / "batchwatch" / "dashboard.py"


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        conn = db.init_db(self.path)
        seed_match(conn)
        weak = seed_match(conn, {**STOCK, "drug_name": "Dolo 650", "batch_no": "DLT-260356"})
        db.set_ai_review(conn, weak, {"verdict": "likely_different", "confidence": 71, "note": "n", "model": "m"})
        db.mark_alert_processed(conn, "old.pdf")
        db.log_event(conn, "watcher", "fetched", "x")
        db.log_event(conn, "bot", "started", "@b")
        db.log_event(conn, "user:5", "approved", "match 1")
        conn.close()

    def run_app(self, path=None):
        with mock.patch.object(db, "DB_PATH", path or self.path):
            return AppTest.from_file(DASHBOARD, default_timeout=30).run()

    def test_headline_counts(self):
        at = self.run_app()
        self.assertFalse(at.exception)
        counts = {m.label: m.value for m in at.metric}
        self.assertEqual(counts["Alerts processed"], "2")
        self.assertEqual(counts["Batches extracted"], "1")
        self.assertEqual((counts["Confirmed"], counts["Uncertain"]), ("1", "1"))

    def test_flagged_table_has_reason_and_status(self):
        flagged = self.run_app().dataframe[0].value
        self.assertEqual(list(flagged["status"]), ["confirmed", "uncertain"])
        for col in ("drug", "batch", "shelf", "qty", "reason", "decision"):
            self.assertIn(col, flagged.columns)
        self.assertIn("identical after normalising", flagged["reason"][0])

    def test_ai_opinion_is_shown_but_does_not_change_the_flag(self):
        flagged = self.run_app().dataframe[0].value
        self.assertEqual(list(flagged["ai"])[1], "likely_different")
        self.assertEqual(list(flagged["ai_conf"])[1], 71)
        self.assertEqual(list(flagged["status"]), ["confirmed", "uncertain"])

    def test_audit_table_filters_by_agent(self):
        at = self.run_app()
        self.assertEqual(set(at.dataframe[1].value["agent"]), {"watcher", "bot", "user:5"})
        at.multiselect[0].set_value(["bot"])
        with mock.patch.object(db, "DB_PATH", self.path):
            at.run()
        self.assertEqual(list(at.dataframe[1].value["agent"]), ["bot"])

    def test_sidebar_says_read_only(self):
        at = self.run_app()
        self.assertEqual(at.sidebar.info[0].value, "Viewer role: read-only, approvals happen in Telegram")

    def test_never_writes_to_the_database(self):
        before = self.path.read_bytes()
        self.run_app()
        self.assertEqual(self.path.read_bytes(), before)
        source = DASHBOARD.read_text(encoding="utf-8")
        self.assertIn("mode=ro", source)
        for verb in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "init_db"):
            self.assertNotIn(verb, source)

    def test_missing_database_warns_and_creates_nothing(self):
        missing = Path(self.tmp.name) / "nope.db"
        at = self.run_app(missing)
        self.assertFalse(at.exception)
        self.assertTrue(at.warning)
        self.assertFalse(missing.exists())

    def test_out_of_date_database_shows_error_not_traceback(self):
        old = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(old)
        conn.execute("CREATE TABLE alert_rows (id INTEGER PRIMARY KEY, report_month TEXT)")
        conn.commit()
        conn.close()
        at = self.run_app(old)
        self.assertFalse(at.exception)
        self.assertTrue(at.error)

    def test_stays_under_100_lines(self):
        self.assertLess(len(DASHBOARD.read_text(encoding="utf-8").splitlines()), 100)


if __name__ == "__main__":
    unittest.main()
