import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from batchwatch import db
from batchwatch.agents import watcher


def payload(*batches: str) -> str:
    return json.dumps({"aaData": [
        {"str_product_name": f"Drug {b}", "str_batch_no": b} for b in batches
    ]})


class AuditLog(unittest.TestCase):
    def test_append_only(self):
        conn = db.init_db(":memory:")
        db.log_event(conn, "test", "step", "hello")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_log SET detail = 'x'")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM audit_log")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 1)

    def test_log_without_conn_prints(self):
        with mock.patch("builtins.print") as p:
            db.log_event(None, "test", "step", "hello")
        p.assert_called_once()


class Migration(unittest.TestCase):
    def test_old_database_gains_brand_name_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE alert_rows (id INTEGER PRIMARY KEY, row_key TEXT UNIQUE, "
                        "drug_name TEXT, batch_no TEXT)")
            old.commit()
            old.close()
            conn = db.init_db(path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(alert_rows)")]
            self.assertIn("brand_name", cols)
            conn.close()

    def test_brand_name_is_stored(self):
        conn = db.init_db(":memory:")
        db.save_alert_rows(conn, [{"drug_name": "D", "batch_no": "B", "brand_name": "Alzomac"}])
        self.assertEqual(conn.execute("SELECT brand_name FROM alert_rows").fetchone()[0], "Alzomac")


class Watcher(unittest.TestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(watcher, "ALERTS_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_second_run_returns_nothing_new(self):
        fetch = mock.Mock(return_value=payload("A1", "A2"))
        first = watcher.find_new_alerts(self.conn, 2026, 9, fetch=fetch)
        second = watcher.find_new_alerts(self.conn, 2026, 9, fetch=fetch)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])

    def test_only_new_batches_returned_and_feed_duplicates_collapse(self):
        watcher.find_new_alerts(self.conn, 2026, 9, fetch=lambda y, m: payload("A1"))
        new = watcher.find_new_alerts(
            self.conn, 2026, 9, fetch=lambda y, m: payload("A1", "a2", "A2 "))
        self.assertEqual([r["batch_no"] for r in new], ["a2"])

    def test_same_batch_different_drug_is_new(self):
        self.assertNotEqual(
            db.row_key({"drug_name": "X", "batch_no": "1"}),
            db.row_key({"drug_name": "Y", "batch_no": "1"}))

    def test_fetch_failure_falls_back_to_cache_and_logs(self):
        watcher.find_new_alerts(self.conn, 2026, 9, fetch=lambda y, m: payload("A1"))

        def boom(y, m):
            raise ConnectionError("down")

        db2 = db.init_db(":memory:")
        rows = watcher.find_new_alerts(db2, 2026, 9, fetch=boom)
        self.assertEqual(len(rows), 1)
        actions = [r[0] for r in db2.execute("SELECT action FROM audit_log")]
        self.assertIn("fetch_failed", actions)
        self.assertIn("used_cache", actions)

    def test_failure_without_cache_raises(self):
        def boom(y, m):
            raise ConnectionError("down")

        with self.assertRaises(RuntimeError):
            watcher.find_new_alerts(self.conn, 2026, 9, fetch=boom)


if __name__ == "__main__":
    unittest.main()
