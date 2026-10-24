import json
import unittest
from pathlib import Path

from batchwatch.agents import matcher, parser

DATA = Path(__file__).resolve().parents[1] / "batchwatch" / "data"


def alert(drug="Telmisartan & Amlodipine Tablets I.P.", batch="DLT-260356", mfr="Daffohills Laboratories Pvt. Ltd."):
    return {"drug_name": drug, "batch_no": batch, "manufacturer": mfr}


def stock(drug="Telmisartan & Amlodipine Tablets I.P.", batch="DLT-260356", mfr="Daffohills Laboratories Pvt. Ltd."):
    return {"drug_name": drug, "batch_no": batch, "manufacturer": mfr}


class Normalise(unittest.TestCase):
    def test_uppercase_and_strip_separators(self):
        self.assertEqual(matcher.normalise_batch("  gtm-94 "), "GTM94")
        self.assertEqual(matcher.normalise_batch("R/0500/347"), "R0500347")
        self.assertEqual(matcher.normalise_batch("DLT  260356 "), "DLT260356")


class Rules(unittest.TestCase):
    def one(self, s, a):
        return matcher.match_stock([s], [a])[0]

    def test_exact_batch_and_drug_is_confirmed(self):
        r = self.one(stock(batch="dlt 260356"), alert())
        self.assertEqual(r["status"], "confirmed")
        self.assertGreater(r["score"], 90)
        self.assertIn("260356", r["reason"])

    def test_exact_batch_but_different_drug_is_uncertain(self):
        r = self.one(stock(drug="Dolo 650 Tablets"), alert())
        self.assertEqual(r["status"], "uncertain")
        self.assertIn("drug", r["reason"].lower())

    def test_exact_batch_but_different_maker_is_uncertain(self):
        r = self.one(stock(mfr="Cipla Ltd"), alert())
        self.assertEqual(r["status"], "uncertain")
        self.assertIn("manufacturer", r["reason"].lower())

    def test_missing_stock_maker_does_not_block_confirmation(self):
        self.assertEqual(self.one(stock(mfr=""), alert())["status"], "confirmed")

    def test_one_char_typo_in_long_batch_is_uncertain_never_confirmed(self):
        r = self.one(stock(batch="DLT-260357X"), alert(batch="DLT-260356X"))
        self.assertEqual(r["status"], "uncertain")
        self.assertIn("similar", r["reason"].lower())

    def test_clearly_different_batch_is_none(self):
        r = self.one(stock(batch="DLT-260221"), alert())
        self.assertEqual(r["status"], "none")
        self.assertIsNone(r["alert"])
        self.assertTrue(r["reason"])

    def test_empty_batch_never_matches(self):
        self.assertEqual(self.one(stock(batch=" - "), alert(batch="/"))["status"], "none")

    def test_one_result_per_stock_row_in_order(self):
        rows = [stock(batch="A1"), stock(batch="DLT-260356")]
        results = matcher.match_stock(rows, [alert()])
        self.assertEqual([r["status"] for r in results], ["none", "confirmed"])


class AgainstPlantedTruth(unittest.TestCase):
    def test_precision_recall_on_planted_batches(self):
        truth = json.loads((DATA / "test_truth.json").read_text(encoding="utf-8"))
        text = (DATA / "alerts" / "nsq_2026-09.json").read_text(encoding="utf-8")
        alerts, _ = parser.parse_nsq_json(text)
        rows = matcher.load_stock_csv(DATA / "stock.csv")
        results = matcher.match_stock(rows, alerts)

        flagged = {r["stock"]["batch_no"] for r in results if r["status"] != "none"}
        planted = {p["stock_batch"] for p in truth["planted"]}
        near = {n["stock_batch"] for n in truth["near_miss"]}

        tp, fp, fn = len(flagged & planted), len(flagged - planted), len(planted - flagged)
        precision, recall = tp / (tp + fp), tp / (tp + fn)
        print(f"\n  stock rows={len(rows)}  planted={len(planted)}  flagged={len(flagged)}")
        print(f"  TP={tp} FP={fp} FN={fn}  precision={precision:.2f}  recall={recall:.2f}")

        self.assertEqual(len(rows), 200)
        self.assertEqual(planted - flagged, set(), "missed a planted batch")
        self.assertEqual(flagged & near, set(), "a near-miss was flagged")
        self.assertEqual((precision, recall), (1.0, 1.0))
        for r in results:
            if r["stock"]["batch_no"] in planted:
                self.assertEqual(r["status"], "confirmed", r["reason"])


if __name__ == "__main__":
    unittest.main()
