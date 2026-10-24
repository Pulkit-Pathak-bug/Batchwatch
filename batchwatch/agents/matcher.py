"""Matcher agent: compares stock rows with alert rows."""
import csv
import re
from pathlib import Path

from rapidfuzz import fuzz, utils

DRUG_MIN = 80
MAKER_MIN = 60
BATCH_FUZZY_MIN = 90
WEIGHTS = {"batch": 0.50, "drug": 0.35, "maker": 0.15}
LEGAL_WORDS = {"pvt", "ltd", "limited", "private", "p", "co", "company", "inc", "llp"}
RANK = {"confirmed": 2, "uncertain": 1}


def normalise_batch(batch: str) -> str:
    """Uppercase and strip whitespace, dashes and slashes."""
    return re.sub(r"[\s\-/]", "", batch or "").upper()


def _maker(name: str) -> str:
    """Lowercase, drop punctuation and legal suffixes (Pvt, Ltd...) so they don't inflate scores."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in LEGAL_WORDS)


def load_stock_csv(path: str | Path) -> list[dict]:
    """Read the pharmacy stock list, keeping every cell exactly as written."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _judge(stock: dict, alert: dict, nb_stock: str, nb_alert: str) -> dict | None:
    """Grade one stock/alert pair, or return None if the batches don't line up."""
    batch = 100.0 if nb_stock == nb_alert else fuzz.ratio(nb_stock, nb_alert, score_cutoff=BATCH_FUZZY_MIN)
    if not batch:
        return None
    drug = fuzz.token_sort_ratio(stock.get("drug_name", ""), alert.get("drug_name", ""),
                                 processor=utils.default_process)
    s_maker, a_maker = _maker(stock.get("manufacturer", "")), _maker(alert.get("manufacturer", ""))
    maker = fuzz.token_set_ratio(s_maker, a_maker) if s_maker and a_maker else None

    weights = {k: w for k, w in WEIGHTS.items() if k != "maker" or maker is not None}
    parts = {"batch": batch, "drug": drug, "maker": maker}
    score = sum(parts[k] * w for k, w in weights.items()) / sum(weights.values())

    maker_txt = f"manufacturer {maker:.0f}% similar" if maker is not None else "manufacturer not comparable"
    head = f"Batch '{stock.get('batch_no', '')}' vs alert batch '{alert.get('batch_no', '')}'"
    if batch == 100.0:
        base = f"{head}: identical after normalising ({nb_alert}); drug names {drug:.0f}% similar; {maker_txt}."
        if drug < DRUG_MIN:
            status, why = "uncertain", f" Same batch number but the drug names differ (need >= {DRUG_MIN}%) - check by hand."
        elif maker is not None and maker < MAKER_MIN:
            status, why = "uncertain", f" Same batch and drug but the manufacturer differs (need >= {MAKER_MIN}%) - check by hand."
        else:
            status, why = "confirmed", " Same batch number and the same drug."
    else:
        base = f"{head}: {batch:.0f}% similar, possible typo or misread; drug names {drug:.0f}% similar; {maker_txt}."
        status, why = "uncertain", " Not an exact batch match - check the physical pack."
    return {"alert": alert, "status": status, "score": round(score, 1), "reason": base + why}


def match_row(stock: dict, alerts: list[dict], normalised: list[str] | None = None) -> dict:
    """Return the best match for one stock row: {stock, alert, status, score, reason}."""
    normalised = normalised or [normalise_batch(a.get("batch_no", "")) for a in alerts]
    nb_stock = normalise_batch(stock.get("batch_no", ""))
    best = None
    if nb_stock:
        for alert, nb_alert in zip(alerts, normalised):
            if not nb_alert:
                continue
            hit = _judge(stock, alert, nb_stock, nb_alert)
            if hit and (best is None or (RANK[hit["status"]], hit["score"]) > (RANK[best["status"]], best["score"])):
                best = hit
    if best is None:
        return {"stock": stock, "alert": None, "status": "none", "score": 0.0,
                "reason": f"No alert batch is an exact or >= {BATCH_FUZZY_MIN}% similar match for "
                          f"'{stock.get('batch_no', '')}'."}
    return {"stock": stock, **best}


def match_stock(stock_rows: list[dict], alert_rows: list[dict]) -> list[dict]:
    """Return one result per stock row, in order; status is confirmed, uncertain or none."""
    normalised = [normalise_batch(a.get("batch_no", "")) for a in alert_rows]
    return [match_row(s, alert_rows, normalised) for s in stock_rows]
