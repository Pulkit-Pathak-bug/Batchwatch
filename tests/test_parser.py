import json
import unittest
from unittest import mock

from batchwatch.agents import parser

RAW = {
    "aaData": [
        {
            "str_product_name": " Alprazolam Tablets IP 0.5 mg",
            "str_batch_no": "60098",
            "dt_manufacturing_date": "Feb-2026",
            "dt_expiry_date": None,
            "str_manufactured_by": "Uniza  Lifecare   Pvt. Ltd.  , Kadi",
            "str_nsq_result": "Fails Dissolution",
            "str_reporting_source": "State lab",
            "str_reported_by_lab_or_state": "LUCKNOW, Uttar Pradesh",
            "dt_reporting_month_year": "SEP-2026",
        },
        {"str_product_name": "Some Drug", "str_batch_no": "  "},
        {"str_product_name": "", "str_batch_no": "X1"},
    ]
}


class ParseNsqJson(unittest.TestCase):
    def test_maps_fields_and_cleans_whitespace(self):
        rows, _ = parser.parse_nsq_json(json.dumps(RAW))
        self.assertEqual(
            rows[0],
            {
                "drug_name": "Alprazolam Tablets IP 0.5 mg",
                "batch_no": "60098",
                "manufacturer": "Uniza Lifecare Pvt. Ltd. , Kadi",
                "mfg_date": "Feb-2026",
                "exp_date": "",
                "reason": "Fails Dissolution",
                "source": "State lab",
                "lab_state": "LUCKNOW, Uttar Pradesh",
                "report_month": "SEP-2026",
            },
        )

    def test_rejects_rows_missing_drug_or_batch(self):
        rows, rejected = parser.parse_nsq_json(RAW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rejected), 2)

    def test_bad_payload_raises(self):
        with self.assertRaises(ValueError):
            parser.parse_nsq_json("<html>blocked</html>")
        with self.assertRaises(ValueError):
            parser.parse_nsq_json({"unexpected": []})


class PdfTables(unittest.TestCase):
    TABLE = [
        ["S.No", "Name of Drug", "Batch No.", "Mfg. Date", "Expiry Date", "Manufactured By", "Reason"],
        ["1", "Paracetamol 500", "PB1", "01/2025", "12/2026", "Acme Labs", "Failed assay"],
        ["2", "", "PB2", "01/2025", "12/2026", "Acme Labs", "Failed assay"],
    ]

    def test_header_mapping(self):
        rows = parser.pdf_tables_to_rows([self.TABLE])
        self.assertEqual(rows[0]["drug_name"], "Paracetamol 500")
        self.assertEqual(rows[0]["batch_no"], "PB1")
        self.assertEqual(rows[0]["manufacturer"], "Acme Labs")
        self.assertEqual(rows[0]["exp_date"], "12/2026")
        self.assertEqual(len(rows), 2)

    def test_pdfplumber_result_used_when_valid(self):
        good = [r for r in parser.pdf_tables_to_rows([self.TABLE]) if r["drug_name"]]
        client = mock.Mock()
        with mock.patch.object(parser, "_pdfplumber_rows", return_value=good):
            self.assertEqual(parser.parse_pdf("x.pdf", client=client), good)
        client.models.generate_content.assert_not_called()

    def test_falls_back_to_gemini_when_validation_fails(self):
        bad = parser.pdf_tables_to_rows([self.TABLE])
        gem = [{"drug_name": "D", "batch_no": "B"}]
        with mock.patch.object(parser, "_pdfplumber_rows", return_value=bad), \
                mock.patch.object(parser, "_gemini_rows", return_value=gem) as g:
            rows = parser.parse_pdf("x.pdf", client=mock.Mock())
        g.assert_called_once()
        self.assertEqual(rows[0]["drug_name"], "D")
        self.assertEqual(rows[0]["reason"], "")


if __name__ == "__main__":
    unittest.main()
