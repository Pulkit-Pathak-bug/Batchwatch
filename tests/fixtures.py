from batchwatch import db
from batchwatch.agents import actor, matcher

ALERT = {
    "drug_name": "Telmisartan & Amlodipine Tablets I.P.", "brand_name": "",
    "batch_no": "DLT-260356", "manufacturer": "Daffohills Laboratories Pvt. Ltd.",
    "mfg_date": "Nov-2025", "exp_date": "Apr-2028",
    "reason": "The sample does not conform to IP in respect of Dissolution",
    "source": "State lab", "lab_state": "LUCKNOW, Uttar Pradesh", "report_month": "SEP-2026",
}
STOCK = {
    "drug_name": "Telmisartan & Amlodipine Tablets I.P.", "batch_no": "dlt 260356",
    "manufacturer": "Daffohills Laboratories Pvt. Ltd.", "exp_date": "Apr-2028",
    "quantity": "21", "shelf_location": "E7-02",
}


def seed_match(conn, stock: dict | None = None, alert: dict | None = None) -> int:
    """Store one alert row and one match against it; return the match id."""
    db.save_alert_rows(conn, [alert or ALERT])
    result = matcher.match_row(stock or STOCK, db.load_alert_rows(conn))
    result["alert_text"] = actor.draft_alert(result)
    result["return_text"] = actor.draft_return_request(result)
    return db.save_match(conn, result)[0]
