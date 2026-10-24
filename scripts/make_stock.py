"""One-off generator: fake 200-row pharmacy stock with 5 planted NSQ batches.

Writes batchwatch/data/stock.csv and batchwatch/data/test_truth.json.
Run from the repo root:  python -m scripts.make_stock
"""
import csv
import json
import random
import re
from pathlib import Path

from rapidfuzz import fuzz

from batchwatch.agents import parser

DATA = Path(__file__).resolve().parents[1] / "batchwatch" / "data"
CACHE = DATA / "alerts" / "nsq_2026-09.json"
COLUMNS = ["drug_name", "batch_no", "manufacturer", "exp_date", "quantity", "shelf_location"]

# (alert batch to copy, how the batch is written in our stock)
PLANTED = [
    ("APRCT-2501", "aprct2501"),
    ("KFM-2501", "KFM-2501"),
    ("DLT-260356", "DLT  260356 "),
    ("SMF-103", "SMF-103"),
    ("MDT-4228", "mdt/4228"),
]
# (alert batch it is "near", a clearly different batch from the same maker)
NEAR_MISSES = [("APRCT-2501", "APRCT-2408"), ("KFM-2501", "KFM-2412"), ("DLT-260356", "DLT-260221")]

FILLER = [
    ("Dolo 650 Tablets", "Micro Labs Ltd"), ("Crocin Advance 500 mg Tablets", "GlaxoSmithKline Pharmaceuticals Ltd"),
    ("Pantocid 40 Tablets", "Sun Pharmaceutical Industries Ltd"), ("Azithral 500 Tablets", "Alembic Pharmaceuticals Ltd"),
    ("Augmentin 625 Duo Tablets", "GlaxoSmithKline Pharmaceuticals Ltd"), ("Cetzine 10 mg Tablets", "GlaxoSmithKline Pharmaceuticals Ltd"),
    ("Allegra 120 mg Tablets", "Sanofi India Ltd"), ("Montair LC Tablets", "Cipla Ltd"),
    ("Glycomet GP 2 Tablets", "USV Pvt Ltd"), ("Telma 40 Tablets", "Glenmark Pharmaceuticals Ltd"),
    ("Amlokind 5 mg Tablets", "Mankind Pharma Ltd"), ("Atorva 10 Tablets", "Zydus Lifesciences Ltd"),
    ("Rosuvas 10 Tablets", "Sun Pharmaceutical Industries Ltd"), ("Shelcal 500 Tablets", "Torrent Pharmaceuticals Ltd"),
    ("Becosules Capsules", "Pfizer Ltd"), ("Limcee 500 mg Chewable Tablets", "Abbott India Ltd"),
    ("Zifi 200 Tablets", "FDC Ltd"), ("Taxim-O 200 Tablets", "Alkem Laboratories Ltd"),
    ("Combiflam Tablets", "Sanofi India Ltd"), ("Volini Gel 30 g", "Sun Pharmaceutical Industries Ltd"),
    ("Betadine Ointment 5%", "Win-Medicare Pvt Ltd"), ("Digene Tablets", "Abbott India Ltd"),
    ("Gelusil MPS Tablets", "Pfizer Ltd"), ("Electral ORS Powder", "FDC Ltd"),
    ("Omez 20 Capsules", "Dr. Reddy's Laboratories Ltd"), ("Rantac 150 Tablets", "J B Chemicals & Pharmaceuticals Ltd"),
    ("Ecosprin 75 Tablets", "USV Pvt Ltd"), ("Metrogyl 400 Tablets", "J B Chemicals & Pharmaceuticals Ltd"),
    ("Norflox 400 Tablets", "Cipla Ltd"), ("Ciplox 500 Tablets", "Cipla Ltd"),
    ("Liv 52 DS Tablets", "Himalaya Wellness Company"), ("Evion 400 Capsules", "Merck Ltd"),
    ("Zincovit Tablets", "Apex Laboratories Pvt Ltd"), ("Neurobion Forte Tablets", "Procter & Gamble Health Ltd"),
    ("Cheston Cold Tablets", "Cipla Ltd"), ("Sinarest Tablets", "Centaur Pharmaceuticals Pvt Ltd"),
    ("Wikoryl Tablets", "Alkem Laboratories Ltd"), ("Meftal Spas Tablets", "Blue Cross Laboratories Pvt Ltd"),
    ("Deriphyllin Tablets", "Zydus Lifesciences Ltd"), ("Thyronorm 50 mcg Tablets", "Abbott India Ltd"),
    ("Eltroxin 50 mcg Tablets", "GlaxoSmithKline Pharmaceuticals Ltd"), ("Insugen-R 40 IU/ml Vial", "Biocon Ltd"),
    ("Human Mixtard 30 Injection", "Novo Nordisk India Pvt Ltd"), ("Pan D Capsules", "Alkem Laboratories Ltd"),
    ("Cyclopam Tablets", "Indoco Remedies Ltd"), ("Levolin Respules", "Cipla Ltd"),
    ("Asthalin Inhaler", "Cipla Ltd"), ("Calpol 500 mg Tablets", "GlaxoSmithKline Pharmaceuticals Ltd"),
    ("Brufen 400 Tablets", "Abbott India Ltd"), ("Dexorange Syrup 200 ml", "Franco-Indian Pharmaceuticals Pvt Ltd"),
    ("Benadryl Cough Syrup 100 ml", "Johnson & Johnson Ltd"), ("Zerodol-SP Tablets", "Ipca Laboratories Ltd"),
    ("Ondem 4 Tablets", "Alkem Laboratories Ltd"), ("Domstal 10 Tablets", "Torrent Pharmaceuticals Ltd"),
    ("Mox 500 Capsules", "Sun Pharmaceutical Industries Ltd"), ("Clavam 625 Tablets", "Alkem Laboratories Ltd"),
]


def norm(batch: str) -> str:
    """Same normalisation the matcher uses, kept local so the generator stands alone."""
    return re.sub(r"[\s\-/]", "", batch).upper()


def main() -> None:
    rng = random.Random(42)
    alerts, _ = parser.parse_nsq_json(CACHE.read_text(encoding="utf-8"))
    by_batch = {r["batch_no"]: r for r in alerts}
    alert_norms = {norm(r["batch_no"]) for r in alerts}

    def shelf() -> str:
        return f"{rng.choice('ABCDEF')}{rng.randint(1, 9)}-{rng.randint(1, 20):02d}"

    def maker(row: dict) -> str:
        return row["manufacturer"].split(",")[0].strip()

    rows, truth = [], {"planted": [], "near_miss": []}
    for alert_batch, written in PLANTED:
        a = by_batch[alert_batch]
        rows.append([a["drug_name"], written, maker(a), a["exp_date"], rng.randint(10, 80), shelf()])
        truth["planted"].append({"stock_batch": written, "alert_batch": alert_batch,
                                 "drug": a["drug_name"], "messy": written != alert_batch})
    for alert_batch, other in NEAR_MISSES:
        a = by_batch[alert_batch]
        assert all(fuzz.ratio(norm(other), n) < 90 for n in alert_norms), other
        rows.append([a["drug_name"], other, maker(a), a["exp_date"], rng.randint(10, 80), shelf()])
        truth["near_miss"].append({"stock_batch": other, "near_alert_batch": alert_batch, "drug": a["drug_name"]})

    used = {norm(r[1]) for r in rows}
    while len(rows) < 200:
        drug, mfr = rng.choice(FILLER)
        batch = rng.choice(["{p}{y}{n:03d}", "{p}-{y}{n:04d}", "{p}/{n:05d}"]).format(
            p="".join(rng.choices("ABCDEFGHKLMNPRSTV", k=rng.randint(2, 3))), y=rng.choice(["24", "25", "26"]), n=rng.randint(1, 9999))
        if norm(batch) in used or any(fuzz.ratio(norm(batch), n) >= 90 for n in alert_norms):
            continue
        used.add(norm(batch))
        exp = f"{rng.choice(['Jan', 'Mar', 'Jun', 'Sep', 'Dec'])}-{rng.choice([2027, 2028, 2029])}"
        rows.append([drug, batch, mfr, exp, rng.randint(5, 150), shelf()])

    rng.shuffle(rows)
    with open(DATA / "stock.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    (DATA / "test_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} stock rows; planted={len(truth['planted'])} near_miss={len(truth['near_miss'])}")


if __name__ == "__main__":
    main()
