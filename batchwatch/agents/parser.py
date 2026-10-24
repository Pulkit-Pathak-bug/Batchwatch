"""Parser agent: extracts NSQ batch rows from an alert PDF."""
from pathlib import Path


def parse_alert_pdf(pdf_path: Path) -> list[dict]:
    """Extract rows as dicts with keys drug_name, batch_no, manufacturer,
    mfg_date, exp_date, reason."""
    raise NotImplementedError
