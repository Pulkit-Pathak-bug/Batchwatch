import unittest

from batchwatch import db
from batchwatch.agents import actor, matcher
from tests.fixtures import ALERT, STOCK, seed_match


class MatchStorage(unittest.TestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")

    def test_save_match_dedupes_on_alert_row_and_stock_batch(self):
        first = seed_match(self.conn)
        result = matcher.match_row(STOCK, db.load_alert_rows(self.conn))
        again, created = db.save_match(self.conn, result)
        self.assertEqual(again, first)
        self.assertFalse(created)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 1)

    def test_get_match_returns_stock_fields_and_nested_alert(self):
        m = db.get_match(self.conn, seed_match(self.conn))
        self.assertEqual((m["status"], m["decision"]), ("confirmed", "pending"))
        self.assertEqual((m["quantity"], m["shelf_location"]), ("21", "E7-02"))
        self.assertEqual(m["alert"]["batch_no"], "DLT-260356")
        self.assertIn("Quarantine", m["alert_text"])
        self.assertIsNone(db.get_match(self.conn, 999))

    def test_pending_lists_confirmed_first_and_hides_decided(self):
        weak = seed_match(self.conn, {**STOCK, "drug_name": "Dolo 650 Tablets"})
        self.assertEqual(db.get_match(self.conn, weak)["status"], "uncertain")
        other = {**ALERT, "batch_no": "ZZ-1", "drug_name": "Other Tablets"}
        strong = seed_match(self.conn, {**STOCK, "batch_no": "ZZ1", "drug_name": "Other Tablets"}, other)
        self.assertEqual([m["id"] for m in db.pending_matches(self.conn)], [strong, weak])
        db.decide_match(self.conn, strong, "approved", 7)
        self.assertEqual([m["id"] for m in db.pending_matches(self.conn)], [weak])

    def test_decide_is_atomic_only_first_press_wins(self):
        mid = seed_match(self.conn)
        self.assertTrue(db.decide_match(self.conn, mid, "approved", 111))
        self.assertFalse(db.decide_match(self.conn, mid, "rejected", 222))
        m = db.get_match(self.conn, mid)
        self.assertEqual((m["decision"], m["decided_by"]), ("approved", "111"))

    def test_recent_log_is_newest_first_and_limited(self):
        for i in range(12):
            db.log_event(self.conn, "t", "step", f"n{i}")
        rows = db.recent_log(self.conn, 10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["detail"], "n11")

    def test_load_alert_rows_has_id_and_brand(self):
        db.save_alert_rows(self.conn, [{**ALERT, "brand_name": "Telma-AM"}])
        row = db.load_alert_rows(self.conn)[0]
        self.assertEqual((row["id"], row["brand_name"]), (1, "Telma-AM"))


class Drafts(unittest.TestCase):
    def match(self, stock):
        return {"stock": stock, "alert": ALERT, "status": "confirmed", "score": 100.0, "reason": "r"}

    def test_alert_text_has_the_facts(self):
        text = actor.draft_alert(self.match(STOCK))
        for needle in ("Telmisartan", "dlt 260356", "E7-02", "21", "Quarantine"):
            self.assertIn(needle, text)

    def test_alert_text_for_uncertain_says_verify(self):
        m = {**self.match(STOCK), "status": "uncertain"}
        self.assertIn("unverified", actor.draft_alert(m).lower())

    def test_return_request_names_supplier_batch_and_defect(self):
        text = actor.draft_return_request(self.match(STOCK))
        for needle in ("Daffohills", "DLT-260356", "dlt 260356", "21", "Dissolution", "LUCKNOW"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
