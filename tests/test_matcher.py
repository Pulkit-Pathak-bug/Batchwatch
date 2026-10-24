import json
import unittest
from pathlib import Path

from batchwatch.agents import matcher, parser

DATA = Path(__file__).resolve().parents[1] / "batchwatch" / "data"


def alert(drug="Telmisartan & Amlodipine Tablets I.P.", batch="DLT-260356",
          mfr="Daffohills Laboratories Pvt. Ltd.", brand=""):
    return {"drug_name": drug, "batch_no": batch, "manufacturer": mfr, "brand_name": brand}


def stock(drug="Telmisartan & Amlodipine Tablets I.P.", batch="DLT-260356",
          mfr="Daffohills Laboratories Pvt. Ltd."):
    return {"drug_name": drug, "batch_no": batch, "manufacturer": mfr}


class Normalise(unittest.TestCase):
    def test_batch_uppercase_and_strip_separators(self):
        self.assertEqual(matcher.normalise_batch("  gtm-94 "), "GTM94")
        self.assertEqual(matcher.normalise_batch("R/0500/347"), "R0500347")
        self.assertEqual(matcher.normalise_batch("DLT  260356 "), "DLT260356")

    def test_drug_expands_abbreviations(self):
        self.assertEqual(matcher.normalise_drug("Diclofenac Sod Inj IP"), "diclofenac sodium injection")
        self.assertEqual(matcher.normalise_drug("Loperamide HCl Tab 2mg"), "loperamide hydrochloride tablets 2 mg")
        self.assertEqual(matcher.normalise_drug("Drotaverine Susp."), "drotaverine suspension")

    def test_drug_drops_pharmacopoeia_tags_and_plural_forms(self):
        self.assertEqual(matcher.normalise_drug("Aspirin Tablets I.P. 150mg"), "aspirin tablets 150 mg")
        self.assertEqual(matcher.normalise_drug("Aspirin Tablet USP"), "aspirin tablets")

    def test_drug_handles_hyphens_and_empty(self):
        self.assertEqual(matcher.normalise_drug("Alzomac-0.5 Tablets"), "alzomac 0.5 tablets")
        self.assertEqual(matcher.normalise_drug(None), "")


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

    def test_maker_short_forms_are_equivalent(self):
        r = self.one(stock(mfr="Daffohills Laboratories Private Limited"), alert())
        self.assertEqual(r["status"], "confirmed")
        r = self.one(stock(mfr="Daffohills Laboratories (P) Ltd"), alert())
        self.assertEqual(r["status"], "confirmed")

    def test_abbreviated_stock_name_confirms(self):
        a = alert(drug="Diclofenac Sodium Injection I.P.", batch="SGALD26004", mfr="SGS Pharmaceuticals Pvt. Ltd.")
        r = self.one(stock(drug="Diclofenac Sod Inj IP", batch="SGALD26004", mfr="SGS Pharmaceuticals Private Limited"), a)
        self.assertEqual(r["status"], "confirmed", r["reason"])

    def test_brand_only_stock_name_confirms_and_reason_names_the_brand(self):
        a = alert(drug="Alprazolam Tablets IP 0.5 mg", batch="60098", mfr="Uniza Lifecare Pvt. Ltd.",
                  brand="Alzomac-0.5 Tablets")
        r = self.one(stock(drug="Alzomac 0.5", batch="60098", mfr="Uniza Lifecare Private Limited"), a)
        self.assertEqual(r["status"], "confirmed", r["reason"])
        self.assertIn("brand", r["reason"].lower())
        self.assertIn("Alzomac-0.5 Tablets", r["reason"])

    def test_brand_does_not_match_when_alert_has_no_brand(self):
        a = alert(drug="Aspirin Tablets IP 150mg", batch="TAS25011AL", mfr="Hindustan Laboratories Ltd.", brand="")
        r = self.one(stock(drug="Ecosprin 75", batch="TAS25011AL", mfr="Hindustan Laboratories Ltd."), a)
        self.assertEqual(r["status"], "uncertain")

    def test_weak_name_on_exact_batch_is_uncertain_not_missed(self):
        a = alert(drug="Loperamide Hydrochloride Tablets IP 2 mg", batch="LOF-26002", mfr="Okvik Pharma Pvt. Ltd.",
                  brand="Lofnec Tablets")
        r = self.one(stock(drug="Anti-diarrhoeal tabs", batch="LOF26002", mfr="Okvik Pharma Pvt Ltd"), a)
        self.assertEqual(r["status"], "uncertain")
        self.assertIn("check by hand", r["reason"])

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

        status = {r["stock"]["batch_no"]: r["status"] for r in results}
        flagged = {b for b, s in status.items() if s != "none"}
        planted = {p["stock_batch"] for p in truth["planted"]}
        near = {n["stock_batch"] for n in truth["near_miss"]}

        tp, fp, fn = len(flagged & planted), len(flagged - planted), len(planted - flagged)
        precision, recall = tp / (tp + fp), tp / (tp + fn)
        print(f"\n  stock rows={len(rows)}  planted={len(planted)}  near-misses={len(near)}  flagged={len(flagged)}")
        for p in truth["planted"]:
            print(f"    {status[p['stock_batch']]:9} {p['kind']:22} stock={p['stock_drug']!r}")
        print(f"  near-misses wrongly flagged={len(flagged & near)}")
        print(f"  TP={tp} FP={fp} FN={fn}  precision={precision:.2f}  recall={recall:.2f}")

        self.assertEqual(len(rows), 200)
        self.assertEqual(planted - flagged, set(), "missed a planted batch")
        self.assertEqual(flagged & near, set(), "a near-miss was flagged")
        self.assertEqual((precision, recall), (1.0, 1.0))
        for p in truth["planted"]:
            if p["kind"] in ("exact_copy", "messy_batch"):
                self.assertEqual(status[p["stock_batch"]], "confirmed", p)
            else:
                self.assertIn(status[p["stock_batch"]], ("confirmed", "uncertain"), p)


if __name__ == "__main__":
    unittest.main()
