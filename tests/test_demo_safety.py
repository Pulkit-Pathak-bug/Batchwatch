import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from batchwatch import bot, db, orchestrator
from batchwatch.agents import watcher
from scripts import reset_demo
from tests.fixtures import seed_match
from tests.test_orchestrator import FEED, HEADER, HIT


def boom(y, m):
    raise ConnectionError("no wifi")


class FallbackBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for patcher in (mock.patch.object(watcher, "ALERTS_DIR", self.dir),
                        mock.patch.dict(os.environ, clear=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop(watcher.OFFLINE_ENV, None)
        self.conn = db.init_db(":memory:")

    def actions(self):
        return [r["action"] for r in db.recent_log(self.conn, 50)]


class OfflineFallback(FallbackBase):
    def seed_cache(self, year=2026, month=9):
        watcher.cache_path(year, month).write_text(FEED, encoding="utf-8")

    def test_live_success_reports_live_source_and_refreshes_cache(self):
        info = {}
        rows = watcher.find_new_alerts(self.conn, 2026, 9, fetch=lambda y, m: FEED, info=info)
        self.assertEqual((len(rows), info["source"]), (1, "live"))
        self.assertTrue(watcher.cache_path(2026, 9).exists())

    def test_failed_fetch_uses_cache_and_logs_it(self):
        self.seed_cache()
        info = {}
        rows = watcher.find_new_alerts(self.conn, 2026, 9, fetch=boom, info=info)
        self.assertEqual((len(rows), info["source"]), (1, "cache"))
        self.assertIn("fetch_failed", self.actions())
        detail = [r["detail"] for r in db.recent_log(self.conn, 50) if r["action"] == "used_cache"][0]
        self.assertIn("used cached data", detail)

    def test_offline_switch_never_touches_the_network(self):
        self.seed_cache()
        os.environ[watcher.OFFLINE_ENV] = "1"
        fetch = mock.Mock(side_effect=AssertionError("network used"))
        info = {}
        watcher.find_new_alerts(self.conn, 2026, 9, fetch=fetch, info=info)
        fetch.assert_not_called()
        self.assertEqual(info["source"], "cache")

    def test_captive_portal_page_is_not_cached_and_falls_back(self):
        self.seed_cache()
        portal = "<html><body>Please log in to venue Wi-Fi</body></html>"
        info = {}
        rows = watcher.find_new_alerts(self.conn, 2026, 9, fetch=lambda y, m: portal, info=info)
        self.assertEqual((len(rows), info["source"]), (1, "cache"))
        self.assertEqual(watcher.cache_path(2026, 9).read_text(encoding="utf-8"), FEED)

    def test_missing_month_falls_back_to_newest_cached_month(self):
        self.seed_cache(2026, 9)
        info = {}
        rows = watcher.find_new_alerts(self.conn, 2026, 11, fetch=boom, info=info)
        self.assertEqual((len(rows), info["source"]), (1, "cache"))
        detail = [r["detail"] for r in db.recent_log(self.conn, 50) if r["action"] == "used_cache"][0]
        self.assertIn("nsq_2026-09.json", detail)

    def test_no_cache_anywhere_raises_a_clear_error(self):
        with self.assertRaises(RuntimeError):
            watcher.find_new_alerts(self.conn, 2026, 9, fetch=boom)

    def test_failed_attempts_are_capped_at_about_twelve_seconds(self):
        self.assertEqual(watcher.TIMEOUT, (3, 10))


class DataSourceShown(FallbackBase):
    def test_orchestrator_records_data_source(self):
        stock = self.dir / "stock.csv"
        stock.write_text(HEADER + HIT, encoding="utf-8")
        live = orchestrator.run_pipeline(self.conn, stock_path=stock, fetch=lambda y, m: FEED, year=2026, month=9)
        cached = orchestrator.run_pipeline(self.conn, stock_path=stock, fetch=boom, year=2026, month=9)
        self.assertEqual((live["data_source"], cached["data_source"]), ("live", "cache"))

    def test_summary_tells_the_audience_which_one(self):
        base = {"new_alert_rows": [], "alert_rows": [], "stock_rows": [], "counts": {"confirmed": 0, "uncertain": 0},
                "new_match_ids": [], "stop_reason": None}
        self.assertIn("live CDSCO", bot._summary({**base, "data_source": "live"}, 0))
        self.assertIn("cached copy", bot._summary({**base, "data_source": "cache"}, 0))


class ResetDemo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "demo.db"
        conn = db.init_db(self.path)
        seed_match(conn)
        db.mark_alert_processed(conn, "old.pdf")
        db.grant_approver(conn, 333, 111)
        db.log_event(conn, "bot", "started", "@x")
        conn.close()

    def open(self):
        conn = db.init_db(self.path)
        self.addCleanup(conn.close)
        return conn

    def counts(self, path=None):
        conn = sqlite3.connect(path or self.path)
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("matches", "alert_rows", "processed_alerts", "audit_log", "approvers")}
        finally:
            conn.close()

    def test_dry_run_reports_counts_and_changes_nothing(self):
        before = self.path.read_bytes()
        result = reset_demo.reset(self.path, backup_dir=self.dir / "bk")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result["counts"]["matches"], 1)
        self.assertIsNone(result["backup"])
        self.assertFalse((self.dir / "bk").exists())

    def test_apply_clears_everything_but_approvers_and_writes_one_reset_entry(self):
        result = reset_demo.reset(self.path, apply=True, backup_dir=self.dir / "bk")
        after = self.counts()
        self.assertEqual((after["matches"], after["alert_rows"], after["processed_alerts"]), (0, 0, 0))
        self.assertEqual(after["approvers"], 1)
        self.assertEqual(after["audit_log"], 1)
        conn = self.open()
        self.assertEqual(db.recent_log(conn, 1)[0]["action"], "reset")
        self.assertEqual(self.counts(result["backup"])["matches"], 1)

    def test_approvers_flag_clears_them_too(self):
        reset_demo.reset(self.path, apply=True, clear_approvers=True, backup_dir=self.dir / "bk")
        self.assertEqual(self.counts()["approvers"], 0)

    def test_audit_log_is_protected_again_after_reset(self):
        reset_demo.reset(self.path, apply=True, backup_dir=self.dir / "bk")
        conn = self.open()
        for sql in ("DELETE FROM audit_log", "UPDATE audit_log SET detail = 'x'"):
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute(sql)

    def test_match_ids_are_never_reused_so_old_buttons_cannot_hit_new_matches(self):
        reset_demo.reset(self.path, apply=True, backup_dir=self.dir / "bk")
        self.assertEqual(seed_match(self.open()), 2)

    def test_old_style_matches_table_is_upgraded_and_keeps_its_id_floor(self):
        conn = db.init_db(self.path)
        conn.executescript("DROP TABLE matches;" + db.MATCHES_SQL.replace(" AUTOINCREMENT", ""))
        conn.execute("INSERT INTO matches (id, alert_row_id, stock_batch, status) VALUES (5, 1, 'B', 'confirmed')")
        conn.commit()
        conn.close()
        reset_demo.reset(self.path, apply=True, backup_dir=self.dir / "bk")
        conn = self.open()
        self.assertIn("AUTOINCREMENT", conn.execute("SELECT sql FROM sqlite_master WHERE name = 'matches'").fetchone()[0])
        self.assertGreater(seed_match(conn), 5)

    def test_cli_dry_run_flag_is_explicit_and_cannot_combine_with_yes(self):
        before = self.path.read_bytes()
        with mock.patch.object(reset_demo.db, "DB_PATH", self.path):
            reset_demo.main(["--dry-run"])
            self.assertEqual(self.path.read_bytes(), before)
            with self.assertRaises(SystemExit):
                reset_demo.main(["--yes", "--dry-run"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_does_nothing_without_yes(self):
        before = self.path.read_bytes()
        with mock.patch.object(reset_demo.db, "DB_PATH", self.path):
            reset_demo.main([])
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
