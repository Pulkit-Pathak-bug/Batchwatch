"""Parser agent: turns CDSCO NSQ data (JSON feed or old PDFs) into AlertRow dicts."""
import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from batchwatch import db

FIELDS = ("drug_name", "brand_name", "batch_no", "manufacturer", "mfg_date", "exp_date",
          "reason", "source", "lab_state", "report_month")

NSQ_KEYS = {
    "drug_name": "str_product_name",
    "brand_name": "str_brand_name",
    "batch_no": "str_batch_no",
    "manufacturer": "str_manufactured_by",
    "mfg_date": "dt_manufacturing_date",
    "exp_date": "dt_expiry_date",
    "reason": "str_nsq_result",
    "source": "str_reporting_source",
    "lab_state": "str_reported_by_lab_or_state",
    "report_month": "dt_reporting_month_year",
}

GEMINI_MODEL = "gemini-flash-latest"
NO_BRAND = {"", "na", "n/a", "nil", "none", "-"}


def clean(value: object) -> str:
    """Stringify, collapse runs of whitespace, treat None as empty."""
    return " ".join(str(value or "").split())


def validate_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split rows into (valid, rejected); drug_name and batch_no must be non-empty."""
    valid = [r for r in rows if r.get("drug_name") and r.get("batch_no")]
    return valid, [r for r in rows if r not in valid]


def parse_nsq_json(payload: str | dict) -> tuple[list[dict], list[dict]]:
    """Convert the CDSCO DataTables response into (valid_rows, rejected_rows)."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        raw_rows = data["aaData"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"not a CDSCO NSQ response: {exc!r}") from exc
    rows = [{f: clean(r.get(k)) for f, k in NSQ_KEYS.items()} for r in raw_rows]
    for row in rows:
        if row["brand_name"].lower() in NO_BRAND:
            row["brand_name"] = ""
    return validate_rows(rows)


def _column(header: str) -> str | None:
    """Map a PDF table header to an AlertRow field (order matters)."""
    h = header.lower()
    if "exp" in h:
        return "exp_date"
    if "batch" in h:
        return "batch_no"
    if "mfg" in h or ("manufactur" in h and "date" in h):
        return "mfg_date"
    if "manufactur" in h or "company" in h:
        return "manufacturer"
    if any(w in h for w in ("reason", "result", "nsq", "remark")):
        return "reason"
    if any(w in h for w in ("name", "drug", "product")):
        return "drug_name"
    return None


def pdf_tables_to_rows(tables: list[list[list[str | None]]]) -> list[dict]:
    """Turn pdfplumber tables (first row = header) into unvalidated AlertRow dicts."""
    rows = []
    for table in tables:
        if not table:
            continue
        cols = [_column(clean(h)) for h in table[0]]
        for cells in table[1:]:
            row = dict.fromkeys(FIELDS, "")
            row["source"] = "CDSCO PDF"
            for field, cell in zip(cols, cells):
                if field:
                    row[field] = clean(cell)
            rows.append(row)
    return rows


def _pdfplumber_rows(pdf_path: Path) -> list[dict]:
    """Read every table in the PDF with pdfplumber."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        tables = [t for page in pdf.pages for t in page.extract_tables()]
    return pdf_tables_to_rows(tables)


class PdfRow(BaseModel):
    """Schema Gemini must fill for each batch in the PDF."""
    drug_name: str
    batch_no: str
    manufacturer: str = ""
    mfg_date: str = ""
    exp_date: str = ""
    reason: str = ""


def _gemini_rows(pdf_path: Path, client) -> list[dict]:
    """Ask Gemini for structured rows; schema-constrained JSON, no free text parsing."""
    from google.genai import types

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=Path(pdf_path).read_bytes(), mime_type="application/pdf"),
            "Extract every Not-of-Standard-Quality drug batch listed in this CDSCO alert.",
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[PdfRow],
        ),
    )
    return [{**dict.fromkeys(FIELDS, ""), **item.model_dump(), "source": "CDSCO PDF (Gemini)"}
            for item in response.parsed]


def parse_pdf(pdf_path: str | Path, client=None, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Parse an old PDF alert: pdfplumber first, Gemini if any row fails validation."""
    pdf_path = Path(pdf_path)
    rows = _pdfplumber_rows(pdf_path)
    valid, rejected = validate_rows(rows)
    db.log_event(conn, "parser", "pdfplumber", f"{pdf_path.name}: {len(valid)} valid, {len(rejected)} rejected")
    if rows and not rejected:
        return valid
    if client is None:
        from dotenv import load_dotenv
        from google import genai

        load_dotenv()
        client = genai.Client()
    rows = [{**dict.fromkeys(FIELDS, ""), **r} for r in _gemini_rows(pdf_path, client)]
    rows, _ = validate_rows(rows)
    db.log_event(conn, "parser", "gemini_fallback", f"{pdf_path.name}: {len(rows)} rows")
    return rows
