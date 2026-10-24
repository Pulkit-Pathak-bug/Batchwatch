import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from batchwatch import db, orchestrator
from batchwatch.agents import watcher

FEED = json.dumps({"aaData": [{
    "str_product_name": "Telmisartan & Amlodipine Tablets I.P.", "str_batch_no": "DLT-260356",
    "str_manufactured_by": "Daffohills Laboratories Pvt. Ltd.", "str_brand_name": "NA",
    "str_nsq_result": "Fails Dissolution", "str_reported_by_lab_or_state": "LUCKNOW, Uttar Pradesh",
}]})
HEADER = "drug_name,batch_no,manufacturer,exp_date,quantity,shelf_location\n"
HIT = "Telmisartan & Amlodipine Tablets I.P.,dlt 260356,Daffohills Laboratories Pvt. Ltd.,Apr-2028,21,E7-02\n"
MISS = "Dolo 650 Tablets,XX-1,Micro Labs Ltd,Jan-2028,5,A1-01\n"


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(watcher, "ALERTS_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = db.init_db(":memory:")

    def run_with(self, stock_csv, fetch=lambda y, m: FEED):
        path = Path(self.tmp.name) / "stock.csv"
        path.write_text(stock_csv, encoding="utf-8")
        return orchestrator.run_pipeline(self.conn, stock_path=path, fetch=fetch, year=2026, month=9)

    def test_end_to_end_saves_match_with_drafts(self):
        state = self.run_with(HEADER + HIT + MISS)
        self.assertIsNone(state["stop_reason"])
        self.assertEqual(state["counts"], {"confirmed": 1, "uncertain": 0})
        self.assertEqual(len(state["new_match_ids"]), 1)
        m = db.get_match(self.conn, state["new_match_ids"][0])
        self.assertEqual((m["shelf_location"], m["decision"]), ("E7-02", "pending"))
        self.assertTrue(m["alert_text"] and m["return_text"])
        actions = [r["action"] for r in db.recent_log(self.conn, 50)]
        for step in ("pipeline_start", "matched", "pipeline_done"):
            self.assertIn(step, actions)

    def test_second_run_creates_no_duplicate_matches(self):
        self.run_with(HEADER + HIT)
        state = self.run_with(HEADER + HIT)
        self.assertEqual(state["new_alert_rows"], [])
        self.assertEqual(state["new_match_ids"], [])
        self.assertEqual(state["pending_total"], 1)

    def test_decided_match_is_not_reopened_by_rerun(self):
        first = self.run_with(HEADER + HIT)["new_match_ids"][0]
        db.decide_match(self.conn, first, "rejected", 1)
        state = self.run_with(HEADER + HIT)
        self.assertEqual((state["new_match_ids"], state["pending_total"]), ([], 0))

    def test_stop_when_stock_is_empty(self):
        state = self.run_with(HEADER)
        self.assertEqual(state["stop_reason"], "stock list is empty")
        self.assertEqual(state["new_match_ids"], [])

    def test_stop_when_nothing_matches(self):
        state = self.run_with(HEADER + MISS)
        self.assertEqual(state["stop_reason"], "no stock row matches any alert")

    def test_fetch_failure_without_cache_propagates(self):
        def boom(y, m):
            raise ConnectionError("down")

        with self.assertRaises(RuntimeError):
            self.run_with(HEADER + HIT, fetch=boom)

    def test_should_stop_is_false_before_any_stage_ran(self):
        self.assertFalse(orchestrator.should_stop({"alert_rows": None, "stock_rows": None, "matches": None}))


if __name__ == "__main__":
    unittest.main()
